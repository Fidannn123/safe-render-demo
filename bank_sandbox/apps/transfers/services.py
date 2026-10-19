"""SIMULATED settlement: idempotent on end_to_end_id, synchronous, Ed25519-signed receipt.

Concurrency: the debtor (and an internal creditor) account rows are locked with SELECT ... FOR UPDATE
in account_id order, then the failure-arm row; the idempotency check runs again under those locks, and
UNIQUE(end_to_end_id) catches the remaining race (same e2e, different debtor).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction

from apps.accounts.iban import iban_valid
from apps.accounts.models import Account, Entry
from apps.transfers.models import FailureArm, Receipt, Transfer
from safepay_bank import clock
from safepay_bank.keys import attestation_key
from safepay_bank.problems import ProblemError
from safepay_bank.signing import canonical_json, sha256_hex, sign_document


@dataclass(frozen=True)
class SubmitResult:
    transfer: Transfer
    created: bool
    delay_response: bool = False


def request_sha256(request: dict[str, Any]) -> str:
    """SHA-256 of the RFC 8785 canonical TransferRequest (spec: Transfer.request_sha256)."""
    return sha256_hex(canonical_json(request))


def transfer_json(transfer: Transfer) -> dict[str, Any]:
    receipt = Receipt.objects.filter(transfer=transfer).first()
    return {
        "transfer_id": str(transfer.transfer_id),
        "end_to_end_id": str(transfer.end_to_end_id),
        "status": transfer.status,
        "request_sha256": transfer.request_sha256,
        "created_at": clock.rfc3339(transfer.created_at),
        "receipt": receipt.document if receipt is not None else None,
    }


def _replay(existing: Transfer, digest: str) -> SubmitResult:
    if existing.request_sha256 != digest:
        raise ProblemError(
            "IDEMPOTENCY_MISMATCH",
            f"end_to_end_id {existing.end_to_end_id} was already used with a different request body",
        )
    return SubmitResult(transfer=existing, created=False)


def get_fail_next() -> FailureArm:
    arm, _ = FailureArm.objects.get_or_create(id=1)
    return arm


def _locked_arm() -> FailureArm:
    """Lock the singleton (created by migration; re-created if a test flush removed it)."""
    FailureArm.objects.get_or_create(id=1)
    return FailureArm.objects.select_for_update().get(id=1)


def arm_fail_next(count: int, reason: str, mode: str) -> FailureArm:
    with transaction.atomic():
        arm = _locked_arm()
        arm.remaining, arm.reason, arm.mode = count, reason, mode
        arm.save()
        return arm


def _build_receipt(
    *, transfer: Transfer, debtor: Account, request: dict[str, Any], failure_reason: str | None
) -> dict[str, Any]:
    receipt: dict[str, Any] = {
        "receipt_id": str(uuid.uuid4()),
        "transfer_id": str(transfer.transfer_id),
        "end_to_end_id": str(transfer.end_to_end_id),
        "status": transfer.status,
        "debtor": {"account_id": str(debtor.account_id), "iban": debtor.iban},
        "creditor": request["creditor"],
        "amount_minor": request["amount_minor"],
        "currency": request["currency"],
        "booked_at": clock.rfc3339(transfer.created_at),
        "failure_reason": failure_reason,
        "bank_id": settings.SAFEPAY_BANK_ID,
        "simulated": True,
    }
    if "remittance_info" in request:
        receipt["remittance_info"] = request["remittance_info"]
    key_id, key = attestation_key()
    return sign_document(receipt, key, key_id)


def submit_transfer(request: dict[str, Any]) -> SubmitResult:
    """Create and settle (or fail) a transfer. `request` is the validated TransferRequest as sent."""
    digest = request_sha256(request)
    e2e = uuid.UUID(request["end_to_end_id"])
    existing = Transfer.objects.filter(end_to_end_id=e2e).first()
    if existing is not None:
        return _replay(existing, digest)

    debtor_id = uuid.UUID(request["debtor_account_id"])
    if not Account.objects.filter(pk=debtor_id).exists():
        raise ProblemError("ACCOUNT_NOT_FOUND", "debtor account not found")

    try:
        with transaction.atomic():
            return _settle(request, digest, e2e, debtor_id)
    except IntegrityError:
        # Same end_to_end_id committed concurrently (UNIQUE): the committed row decides.
        existing = Transfer.objects.filter(end_to_end_id=e2e).first()
        if existing is None:
            raise
        return _replay(existing, digest)


def _settle(request: dict[str, Any], digest: str, e2e: uuid.UUID, debtor_id: uuid.UUID) -> SubmitResult:
    amount = int(request["amount_minor"])
    currency = request["currency"]
    creditor_iban = request["creditor"]["iban"]

    internal_creditor_id = (
        Account.objects.filter(iban=creditor_iban, currency=currency)
        .values_list("account_id", flat=True)
        .first()
    )
    lock_ids = sorted({debtor_id} | ({internal_creditor_id} if internal_creditor_id else set()))
    locked = {
        a.account_id: a for a in Account.objects.select_for_update().filter(pk__in=lock_ids).order_by("pk")
    }
    debtor = locked[debtor_id]
    if debtor.currency != currency:
        raise ProblemError(
            "VALIDATION_FAILED",
            "currency does not match the debtor account",
            errors=[{"loc": ["body", "currency"], "msg": f"debtor account currency is {debtor.currency}"}],
        )

    existing = Transfer.objects.filter(end_to_end_id=e2e).first()
    if existing is not None:
        return _replay(existing, digest)

    arm = _locked_arm()
    failure_reason: str | None = None
    delay = False
    if arm.remaining > 0:
        arm.remaining -= 1
        arm.save(update_fields=["remaining"])
        if arm.mode == FailureArm.Mode.FAIL:
            failure_reason = arm.reason
        else:
            delay = True
    if failure_reason is None:
        if not iban_valid(creditor_iban):
            failure_reason = "CREDITOR_ACCOUNT_INVALID"
        elif debtor.balance_minor < amount:
            failure_reason = "INSUFFICIENT_FUNDS"

    now = clock.now()
    with transaction.atomic():  # savepoint: UNIQUE(end_to_end_id) race surfaces here
        transfer = Transfer.objects.create(
            end_to_end_id=e2e,
            request_sha256=digest,
            request=request,
            debtor=debtor,
            status=Transfer.Status.FAILED if failure_reason else Transfer.Status.SETTLED,
            created_at=now,
        )
    if failure_reason is None:
        _book(transfer, debtor, locked.get(internal_creditor_id) if internal_creditor_id else None, request)
    document = _build_receipt(
        transfer=transfer, debtor=debtor, request=request, failure_reason=failure_reason
    )
    Receipt.objects.create(receipt_id=uuid.UUID(document["receipt_id"]), transfer=transfer, document=document)
    return SubmitResult(transfer=transfer, created=True, delay_response=delay)


def _book(transfer: Transfer, debtor: Account, creditor: Account | None, request: dict[str, Any]) -> None:
    amount = int(request["amount_minor"])
    remittance = request.get("remittance_info", "")
    Entry.objects.create(
        account=debtor,
        transfer_id=transfer.transfer_id,
        end_to_end_id=transfer.end_to_end_id,
        direction=Entry.Direction.DEBIT,
        amount_minor=amount,
        counterparty=request["creditor"],
        remittance_info=remittance,
        booked_at=transfer.created_at,
    )
    debtor.balance_minor -= amount
    debtor.save(update_fields=["balance_minor"])
    if creditor is not None:
        if creditor.pk == debtor.pk:
            creditor = debtor
        Entry.objects.create(
            account=creditor,
            transfer_id=transfer.transfer_id,
            end_to_end_id=transfer.end_to_end_id,
            direction=Entry.Direction.CREDIT,
            amount_minor=amount,
            counterparty={"country": debtor.iban[:2], "iban": debtor.iban, "name": debtor.owner_name},
            remittance_info=remittance,
            booked_at=transfer.created_at,
        )
        creditor.balance_minor += amount
        creditor.save(update_fields=["balance_minor"])
