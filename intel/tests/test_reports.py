"""POST /v1/reports admission and DELETE /v1/reports/{id} retraction."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from apps.reports.models import Receipt, Report
from safepay_intel.crypto import load_public_key_b64_file, verify_object

from .conftest import OTHER_PAYEE_IBAN, PAYEE_IBAN, MemberHandle, problem, validate_spec

pytestmark = pytest.mark.django_db

URL = "/v1/reports"
MakeMember = Callable[..., MemberHandle]
MakeReceipt = Callable[..., dict[str, Any]]


def _resign(m: MemberHandle, report: dict[str, Any]) -> dict[str, Any]:
    from safepay_intel.crypto import sign_object  # noqa: PLC0415

    body = {k: v for k, v in report.items() if k != "sig"}
    body["sig"] = sign_object(m.key, body)
    return body


def test_spec_example_validates_with_our_fixture_shape(
    make_member: MakeMember, make_receipt: MakeReceipt
) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    validate_spec("schemas/Report.json", report)
    validate_spec("schemas/Receipt.json", report["receipt"])


def test_admitted_report_is_stored_with_receipt(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    response = m.call("POST", URL, report)
    assert response.status_code == 201, response.content
    body = json.loads(response.content)
    validate_spec("openapi-intel.yaml#/components/schemas/ReportRecord", body)
    assert body["status"] == "ADMITTED"
    assert body["payee_counted"] is True
    stored = Report.objects.get(report_id=report["report_id"])
    expires = stored.expires_at - stored.admitted_at
    assert expires == timedelta(days=30)
    receipt = Receipt.objects.get(report=stored)
    assert str(receipt.receipt_id) == report["receipt"]["receipt_id"]
    from django.conf import settings  # noqa: PLC0415

    bank_key = load_public_key_b64_file(settings.SAFEPAY_BANK_ATTESTATION_PUB_FILE)
    assert verify_object(bank_key, json.loads(receipt.signed_receipt))


def test_same_report_id_is_idempotent(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    assert m.call("POST", URL, report).status_code == 201
    again = m.call("POST", URL, report)
    assert again.status_code == 200
    assert json.loads(again.content)["report_id"] == report["report_id"]
    assert Report.objects.count() == 1


def test_report_id_reuse_with_other_content_rejected(
    make_member: MakeMember, make_receipt: MakeReceipt
) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    assert m.call("POST", URL, report).status_code == 201
    changed = _resign(m, {**report, "category": "FAKE_VENDOR"})
    problem(m.call("POST", URL, changed), 409, "DUPLICATE_REPORT")


# ------------------------------------------------------------------ member signature


def test_forged_member_signature_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    report["category"] = "FAKE_VENDOR"  # tampered after signing
    problem(m.call("POST", URL, report), 401, "SIGNATURE_INVALID")


def test_report_for_another_member_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    a, b = make_member(), make_member()
    report = a.report(make_receipt(a))
    problem(b.call("POST", URL, report), 401, "SIGNATURE_INVALID")


# ------------------------------------------------------------------ bank receipt


def test_forged_receipt_signature_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m, sign_key=Ed25519PrivateKey.generate())
    problem(m.call("POST", URL, m.report(receipt)), 422, "RECEIPT_INVALID")


def test_tampered_receipt_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m)
    receipt["amount_minor"] = 1  # altered after the bank signed it; the member re-signs the report
    problem(m.call("POST", URL, m.report(receipt)), 422, "RECEIPT_INVALID")


def test_receipt_with_unpinned_key_id_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m, key_id="bank:attestation:2")
    problem(m.call("POST", URL, m.report(receipt)), 422, "RECEIPT_INVALID")


def test_failed_receipt_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m, status="FAILED", failure_reason="INSUFFICIENT_FUNDS")
    problem(m.call("POST", URL, m.report(receipt)), 422, "RECEIPT_INVALID")


def test_old_receipt_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m, booked_at=datetime.now(UTC) - timedelta(days=91))
    problem(m.call("POST", URL, m.report(receipt)), 422, "RECEIPT_TOO_OLD")


def test_receipt_at_89_days_accepted(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m, booked_at=datetime.now(UTC) - timedelta(days=89))
    assert m.call("POST", URL, m.report(receipt)).status_code == 201


def test_future_receipt_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m, booked_at=datetime.now(UTC) + timedelta(hours=1))
    problem(m.call("POST", URL, m.report(receipt)), 422, "RECEIPT_INVALID")


def test_payee_mismatch_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    receipt = make_receipt(m)
    report = m.report(receipt, payee={"country": "DE", "iban": OTHER_PAYEE_IBAN})
    problem(m.call("POST", URL, report), 422, "PAYEE_MISMATCH")


def test_receipt_paid_by_another_member_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    payer, reporter = make_member(), make_member()
    receipt = make_receipt(payer)  # genuine receipt, but the debtor account belongs to `payer`
    problem(reporter.call("POST", URL, reporter.report(receipt)), 422, "PAYER_NOT_MEMBER")


def test_debtor_iban_must_match_registered_account(
    make_member: MakeMember, make_receipt: MakeReceipt
) -> None:
    m = make_member()
    receipt = make_receipt(m, debtor={"account_id": m.account_id, "iban": "GB29NWBK60161331926819"})
    if m.iban == "GB29NWBK60161331926819":  # pragma: no cover (fixture IBAN rotation)
        pytest.skip("fixture collision")
    problem(m.call("POST", URL, m.report(receipt)), 422, "PAYER_NOT_MEMBER")


def test_malformed_report_rejected(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    del report["evidence_sha256"]
    problem(m.call("POST", URL, report), 422, "VALIDATION_FAILED")


# ------------------------------------------------------------------ counting, expiry, retraction


def test_one_active_report_per_member_and_payee(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    assert m.call("POST", URL, m.report(make_receipt(m))).status_code == 201
    problem(m.call("POST", URL, m.report(make_receipt(m))), 409, "DUPLICATE_REPORT")
    other_payee = make_receipt(m, payee_iban=OTHER_PAYEE_IBAN)
    assert m.call("POST", URL, m.report(other_payee)).status_code == 201


def test_expired_report_no_longer_blocks_and_reports_expired(
    make_member: MakeMember, make_receipt: MakeReceipt
) -> None:
    m = make_member()
    first = m.report(make_receipt(m))
    assert m.call("POST", URL, first).status_code == 201
    Report.objects.filter(report_id=first["report_id"]).update(
        expires_at=datetime.now(UTC) - timedelta(seconds=1)
    )
    replay = m.call("POST", URL, first)
    assert replay.status_code == 200
    assert json.loads(replay.content)["status"] == "EXPIRED"
    assert json.loads(replay.content)["payee_counted"] is False
    assert m.call("POST", URL, m.report(make_receipt(m))).status_code == 201


def test_retraction_by_reporter(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    report = m.report(make_receipt(m))
    assert m.call("POST", URL, report).status_code == 201
    path = f"{URL}/{report['report_id']}"
    assert m.call("DELETE", path).status_code == 204
    assert m.call("DELETE", path).status_code == 204  # idempotent
    assert Report.objects.get(report_id=report["report_id"]).retracted_at is not None
    replay = m.call("POST", URL, report)
    assert json.loads(replay.content)["status"] == "RETRACTED"
    assert m.call("POST", URL, m.report(make_receipt(m))).status_code == 201  # may report again


def test_retraction_by_non_reporter_forbidden(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    reporter, other = make_member(), make_member()
    report = reporter.report(make_receipt(reporter))
    assert reporter.call("POST", URL, report).status_code == 201
    problem(other.call("DELETE", f"{URL}/{report['report_id']}"), 403, "NOT_REPORTER")
    assert Report.objects.get(report_id=report["report_id"]).retracted_at is None


def test_retraction_of_unknown_report(make_member: MakeMember) -> None:
    m = make_member()
    problem(m.call("DELETE", f"{URL}/abababab-cdcd-4efe-8f0f-121212121212"), 404, "NOT_FOUND")
    problem(m.call("DELETE", f"{URL}/not-a-uuid"), 404, "NOT_FOUND")


def test_payee_iban_is_normalised(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    assert m.call("POST", URL, m.report(make_receipt(m, payee_iban=PAYEE_IBAN))).status_code == 201
    assert Report.objects.get().payee_iban == PAYEE_IBAN
