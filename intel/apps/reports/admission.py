"""Report admission and retraction (spec/schemas/Report.json, openapi-intel.yaml /v1/reports).

Admission, in order (first failure wins):
  1. report.member_id is the authenticated member and report.key_id is one of its live keys, and the
     member signature verifies over RFC 8785 JSON of the report without sig   -> SIGNATURE_INVALID
  2. same report_id already stored: identical signed report -> idempotent replay (200), else
                                                                                 -> DUPLICATE_REPORT
  3. receipt key_id is the pinned bank attestation key and its signature verifies -> RECEIPT_INVALID
  4. receipt.status SETTLED                                                      -> RECEIPT_INVALID
  5. report.payee (country, IBAN) equals receipt.creditor                        -> PAYEE_MISMATCH
  6. receipt.debtor account (id and IBAN) belongs to the member                  -> PAYER_NOT_MEMBER
  7. receipt.booked_at not in the future (300 s skew), within receipt_max_age_days -> RECEIPT_INVALID /
                                                                                    RECEIPT_TOO_OLD
  8. no active (not retracted, not expired) report by this member for this payee -> DUPLICATE_REPORT
Then: report row + append-only receipt row in one transaction; expires after report_expiry_days.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from django.conf import settings
from django.db import transaction

from apps.members.auth import MemberContext
from apps.members.models import Member, MemberAccount, MemberKey
from apps.oprf.payee import iban_valid, normalise_iban
from apps.reports.models import Receipt, Report
from apps.reports.schemas import ReportIn
from safepay_intel.crypto import (
    KeyFormatError,
    canonical_json,
    load_public_key_b64_file,
    public_key_from_raw,
    sha256_hex,
    verify_object,
)
from safepay_intel.policy import network_policy
from safepay_intel.problems import ProblemError

FUTURE_SKEW = timedelta(seconds=300)
ReportState = Literal["ADMITTED", "RETRACTED", "EXPIRED"]


@dataclass(frozen=True)
class Admission:
    report: Report
    created: bool


def parse_ts(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    if ts.tzinfo is None:
        raise ValueError("timestamp must carry a zone")
    return ts.astimezone(UTC)


def report_status(report: Report, now: datetime) -> ReportState:
    if report.retracted_at is not None:
        return "RETRACTED"
    if now >= report.expires_at:
        return "EXPIRED"
    return "ADMITTED"


def _verify_member_signature(ctx: MemberContext, payload: ReportIn, raw: dict[str, Any]) -> None:
    if payload.member_id != str(ctx.member.id):
        raise ProblemError("SIGNATURE_INVALID", "report member_id is not the authenticated member")
    key = MemberKey.objects.filter(key_id=payload.key_id, member=ctx.member, revoked_at__isnull=True).first()
    if key is None:
        raise ProblemError("SIGNATURE_INVALID", "report key_id is not a live key of this member")
    try:
        public_key = public_key_from_raw(bytes(key.public_key))
    except KeyFormatError as exc:
        raise ProblemError("SIGNATURE_INVALID", "stored key malformed") from exc
    if not verify_object(public_key, raw):
        raise ProblemError("SIGNATURE_INVALID", "report signature does not verify")


def _verify_receipt(payload: ReportIn, raw_receipt: dict[str, Any]) -> None:
    receipt = payload.receipt
    if receipt.key_id != settings.SAFEPAY_BANK_ATTESTATION_KEY_ID:
        raise ProblemError("RECEIPT_INVALID", "receipt not signed by the pinned bank attestation key")
    bank_key = load_public_key_b64_file(settings.SAFEPAY_BANK_ATTESTATION_PUB_FILE)
    if not verify_object(bank_key, raw_receipt):
        raise ProblemError("RECEIPT_INVALID", "bank signature does not verify")
    if receipt.status != "SETTLED" or receipt.failure_reason is not None:
        raise ProblemError("RECEIPT_INVALID", "receipt is not SETTLED")


def admit(ctx: MemberContext, payload: ReportIn, raw: dict[str, Any], now: datetime) -> Admission:
    _verify_member_signature(ctx, payload, raw)
    signed = canonical_json(raw)
    request_sha = sha256_hex(signed)

    existing = Report.objects.filter(report_id=payload.report_id).first()
    if existing is not None:
        if existing.member_id == ctx.member.id and existing.request_sha256 == request_sha:
            return Admission(existing, created=False)
        raise ProblemError("DUPLICATE_REPORT", "report_id already used for a different report")

    raw_receipt = raw["receipt"]
    _verify_receipt(payload, raw_receipt)
    receipt = payload.receipt

    country = payload.payee.country
    iban = normalise_iban(payload.payee.iban)
    if not iban_valid(iban):
        raise ProblemError(
            "VALIDATION_FAILED",
            "payee IBAN fails mod-97",
            [{"loc": ["body", "payee", "iban"], "msg": "mod-97"}],
        )
    if (receipt.creditor.country, normalise_iban(receipt.creditor.iban)) != (country, iban):
        raise ProblemError("PAYEE_MISMATCH")

    owns = MemberAccount.objects.filter(
        account_id=receipt.debtor.account_id, member=ctx.member, iban=normalise_iban(receipt.debtor.iban)
    ).exists()
    if not owns:
        raise ProblemError("PAYER_NOT_MEMBER")

    booked_at = parse_ts(receipt.booked_at)
    if booked_at > now + FUTURE_SKEW:
        raise ProblemError("RECEIPT_INVALID", "receipt booked in the future")
    policy = network_policy()
    if now - booked_at > timedelta(days=policy.receipt_max_age_days):
        raise ProblemError("RECEIPT_TOO_OLD", f"older than {policy.receipt_max_age_days} days")

    with transaction.atomic():
        Member.objects.select_for_update().get(pk=ctx.member.pk)  # serialise admissions per member
        if Report.objects.filter(report_id=payload.report_id).exists():
            raise ProblemError("DUPLICATE_REPORT", "report_id already used")
        active = Report.objects.filter(
            member=ctx.member,
            payee_country=country,
            payee_iban=iban,
            retracted_at__isnull=True,
            expires_at__gt=now,
        ).exists()
        if active:
            raise ProblemError("DUPLICATE_REPORT")
        report = Report.objects.create(
            report_id=payload.report_id,
            member=ctx.member,
            payee_country=country,
            payee_iban=iban,
            category=payload.category,
            evidence_sha256=payload.evidence_sha256,
            key_id=payload.key_id,
            request_sha256=request_sha,
            signed_report=signed.decode("utf-8"),
            admitted_at=now,
            expires_at=now + timedelta(days=policy.report_expiry_days),
        )
        Receipt.objects.create(
            report=report,
            receipt_id=receipt.receipt_id,
            key_id=receipt.key_id,
            booked_at=booked_at,
            signed_receipt=canonical_json(raw_receipt).decode("utf-8"),
        )
    return Admission(report, created=True)


def retract(ctx: MemberContext, report_id: str, now: datetime) -> None:
    with transaction.atomic():
        report = Report.objects.select_for_update().filter(report_id=report_id).first()
        if report is None:
            raise ProblemError("NOT_FOUND", "report not found")
        if report.member_id != ctx.member.id:
            raise ProblemError("NOT_REPORTER")
        if report.retracted_at is None:
            report.retracted_at = now
            report.save(update_fields=["retracted_at"])
