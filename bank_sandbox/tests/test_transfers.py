"""POST/GET /v1/transfers: idempotency on end_to_end_id, signed receipts, failures, fail-next."""

from __future__ import annotations

import json
import uuid
from typing import Any
from unittest import mock

import pytest

from apps.accounts.models import Account, Entry
from apps.transfers.models import Receipt, Transfer
from apps.transfers.services import request_sha256
from safepay_bank.signing import verify_document
from tests.conftest import BANK_KEY_ID, VENDOR_IBAN, Keys, SignedClient, new_account, transfer_request

pytestmark = pytest.mark.django_db


def body(response: Any) -> Any:
    return json.loads(response.content)


def balance(account: Account) -> int:
    return Account.objects.get(pk=account.pk).balance_minor


def assert_valid_transfer(data: dict[str, Any], keys: Keys, validate: Any) -> dict[str, Any]:
    validate("Transfer", data)
    receipt: dict[str, Any] = data["receipt"]
    validate("Receipt.json", receipt)
    assert receipt["key_id"] == BANK_KEY_ID
    assert verify_document(receipt, keys.bank_public)
    assert receipt["transfer_id"] == data["transfer_id"]
    assert receipt["end_to_end_id"] == data["end_to_end_id"]
    assert receipt["status"] == data["status"]
    return receipt


def test_settles_with_signed_receipt(bank: SignedClient, account: Account, keys: Keys, validate: Any) -> None:
    req = transfer_request(account, 125_000)
    response = bank.post("/v1/transfers", req)
    assert response.status_code == 201, response.content
    data = body(response)
    receipt = assert_valid_transfer(data, keys, validate)
    assert data["status"] == "SETTLED"
    assert receipt["failure_reason"] is None
    assert receipt["creditor"] == req["creditor"]
    assert receipt["debtor"] == {"account_id": str(account.account_id), "iban": account.iban}
    assert receipt["amount_minor"] == 125_000
    assert receipt["remittance_info"] == "INV-1042"
    assert receipt["simulated"] is True
    assert receipt["bank_id"] == "SAFEPAY-SANDBOX"
    assert data["request_sha256"] == request_sha256(req)
    assert balance(account) == account.balance_minor - 125_000


def test_same_e2e_same_body_returns_original(bank: SignedClient, account: Account) -> None:
    req = transfer_request(account)
    first = bank.post("/v1/transfers", req)
    # Same body with a different key order and whitespace: RFC 8785 makes them identical.
    reordered = json.dumps(dict(reversed(list(req.items()))), indent=2)
    second = bank.post("/v1/transfers", reordered.encode())
    assert first.status_code == 201
    assert second.status_code == 200
    assert body(first) == body(second)
    assert Transfer.objects.filter(end_to_end_id=req["end_to_end_id"]).count() == 1
    assert Receipt.objects.count() == 1
    assert balance(account) == account.balance_minor - req["amount_minor"]


def test_same_e2e_different_body_is_409(bank: SignedClient, account: Account, validate: Any) -> None:
    req = transfer_request(account)
    assert bank.post("/v1/transfers", req).status_code == 201
    response = bank.post("/v1/transfers", {**req, "amount_minor": req["amount_minor"] + 1})
    assert response.status_code == 409
    problem = body(response)
    assert problem["code"] == "IDEMPOTENCY_MISMATCH"
    validate("Problem.json", problem)
    # absent vs present optional field is a different body too
    without_remittance = {k: v for k, v in req.items() if k != "remittance_info"}
    assert body(bank.post("/v1/transfers", without_remittance))["code"] == "IDEMPOTENCY_MISMATCH"
    assert Transfer.objects.count() == 1
    assert balance(account) == account.balance_minor - req["amount_minor"]


def test_insufficient_funds_gives_failed_receipt(bank: SignedClient, keys: Keys, validate: Any) -> None:
    poor = new_account(balance=1_000)
    response = bank.post("/v1/transfers", transfer_request(poor, 1_001))
    assert response.status_code == 201
    data = body(response)
    receipt = assert_valid_transfer(data, keys, validate)
    assert data["status"] == "FAILED"
    assert receipt["failure_reason"] == "INSUFFICIENT_FUNDS"
    assert balance(poor) == 1_000
    assert not Entry.objects.filter(account=poor, direction="DEBIT").exists()


def test_exact_balance_settles_to_zero(bank: SignedClient) -> None:
    acct = new_account(balance=5_000)
    assert body(bank.post("/v1/transfers", transfer_request(acct, 5_000)))["status"] == "SETTLED"
    assert balance(acct) == 0


def test_invalid_creditor_iban_gives_failed_receipt(
    bank: SignedClient, account: Account, keys: Keys, validate: Any
) -> None:
    req = transfer_request(account, creditor={"country": "GB", "iban": "GB00WEST12345698765432"})
    data = body(bank.post("/v1/transfers", req))
    receipt = assert_valid_transfer(data, keys, validate)
    assert receipt["failure_reason"] == "CREDITOR_ACCOUNT_INVALID"
    assert balance(account) == account.balance_minor


def test_internal_creditor_is_credited(bank: SignedClient, account: Account) -> None:
    vendor = new_account(balance=0, name="Vendor (SYNTHETIC)")
    req = transfer_request(account, 7_000, creditor={"country": "GB", "iban": vendor.iban})
    assert body(bank.post("/v1/transfers", req))["status"] == "SETTLED"
    assert balance(vendor) == 7_000
    credit = Entry.objects.get(account=vendor, direction="CREDIT")
    assert credit.counterparty["iban"] == account.iban


def test_unknown_debtor_is_404(bank: SignedClient, validate: Any) -> None:
    req = transfer_request(new_account(), debtor_account_id=str(uuid.uuid4()))
    response = bank.post("/v1/transfers", req)
    assert response.status_code == 404
    assert body(response)["code"] == "ACCOUNT_NOT_FOUND"
    validate("Problem.json", body(response))


@pytest.mark.parametrize(
    "mutation",
    [
        {"amount_minor": 0},
        {"amount_minor": -5},
        {"amount_minor": "100"},
        {"amount_minor": 100.0},
        {"amount_minor": True},
        {"currency": "usd"},
        {"end_to_end_id": "AAAAAAAA-BBBB-4CCC-8DDD-EEEEEEEEEEEE"},
        {"remittance_info": None},
        {"remittance_info": "x" * 141},
        {"unexpected": 1},
        {"creditor": {"country": "GB", "iban": VENDOR_IBAN, "bic": "X"}},
        {"creditor": {"country": "GB", "iban": "gb82west12345698765432"}},
    ],
)
def test_invalid_body_is_422(
    bank: SignedClient, account: Account, mutation: dict[str, Any], validate: Any
) -> None:
    response = bank.post("/v1/transfers", transfer_request(account) | mutation)
    assert response.status_code == 422, response.content
    problem = body(response)
    assert problem["code"] == "VALIDATION_FAILED"
    assert problem["errors"]
    validate("Problem.json", problem)
    assert Transfer.objects.count() == 0


def test_missing_field_and_bad_json_are_422(bank: SignedClient, account: Account) -> None:
    req = transfer_request(account)
    del req["currency"]
    assert body(bank.post("/v1/transfers", req))["code"] == "VALIDATION_FAILED"
    assert body(bank.post("/v1/transfers", b"{not json"))["code"] == "VALIDATION_FAILED"


def test_currency_mismatch_is_422(bank: SignedClient) -> None:
    eur = new_account(currency="EUR")
    response = bank.post("/v1/transfers", transfer_request(eur))
    assert response.status_code == 422
    assert body(response)["code"] == "VALIDATION_FAILED"


def test_get_transfer(bank: SignedClient, account: Account, keys: Keys, validate: Any) -> None:
    req = transfer_request(account)
    created = body(bank.post("/v1/transfers", req))
    response = bank.get(f"/v1/transfers/{req['end_to_end_id']}")
    assert response.status_code == 200
    assert body(response) == created
    assert_valid_transfer(body(response), keys, validate)


def test_get_unknown_transfer_is_404(bank: SignedClient, validate: Any) -> None:
    response = bank.get(f"/v1/transfers/{uuid.uuid4()}")
    assert response.status_code == 404
    assert body(response)["code"] == "NOT_FOUND"
    validate("Problem.json", body(response))


def test_get_account(bank: SignedClient, account: Account, validate: Any) -> None:
    response = bank.get(f"/v1/accounts/{account.account_id}")
    assert response.status_code == 200
    data = body(response)
    validate("Account", data)
    assert data["balance_minor"] == account.balance_minor
    assert data["simulated"] is True
    missing = bank.get(f"/v1/accounts/{uuid.uuid4()}")
    assert missing.status_code == 404
    assert body(missing)["code"] == "ACCOUNT_NOT_FOUND"
    assert bank.get("/v1/accounts/not-a-uuid").status_code == 422


# ------------------------------------------------------------------ failure injection
def arm(bank: SignedClient, **payload: Any) -> Any:
    return bank.post("/admin/fail-next", payload)


@pytest.mark.parametrize("reason", ["INSUFFICIENT_FUNDS", "CREDITOR_ACCOUNT_INVALID", "INJECTED_FAILURE"])
def test_fail_next_produces_failed_receipts(
    bank: SignedClient, account: Account, keys: Keys, validate: Any, reason: str
) -> None:
    response = arm(bank, count=2, reason=reason)
    assert response.status_code == 200
    validate("FailNextState", body(response))
    assert body(response) == {"remaining": 2, "reason": reason, "mode": "fail"}
    for _ in range(2):
        data = body(bank.post("/v1/transfers", transfer_request(account)))
        receipt = assert_valid_transfer(data, keys, validate)
        assert data["status"] == "FAILED"
        assert receipt["failure_reason"] == reason
    assert balance(account) == account.balance_minor
    third = body(bank.post("/v1/transfers", transfer_request(account)))
    assert third["status"] == "SETTLED"


def test_fail_next_not_consumed_by_replay_or_rejection(bank: SignedClient, account: Account) -> None:
    req = transfer_request(account)
    bank.post("/v1/transfers", req)
    arm(bank, count=1, reason="INJECTED_FAILURE")
    assert bank.post("/v1/transfers", req).status_code == 200  # replay
    assert bank.post("/v1/transfers", transfer_request(account, 0)).status_code == 422
    assert body(bank.post("/v1/transfers", transfer_request(account)))["status"] == "FAILED"


def test_fail_next_zero_disarms(bank: SignedClient, account: Account) -> None:
    arm(bank, count=5, reason="INJECTED_FAILURE")
    assert body(arm(bank, count=0, reason="INJECTED_FAILURE"))["remaining"] == 0
    assert body(bank.post("/v1/transfers", transfer_request(account)))["status"] == "SETTLED"


@pytest.mark.parametrize(
    "payload",
    [
        {"count": 101, "reason": "INJECTED_FAILURE"},
        {"count": 1, "reason": "NOPE"},
        {"count": 1, "reason": "INJECTED_FAILURE", "mode": "explode"},
        {"count": 1},
    ],
)
def test_fail_next_validation(bank: SignedClient, payload: dict[str, Any]) -> None:
    response = arm(bank, **payload)
    assert response.status_code == 422
    assert body(response)["code"] == "VALIDATION_FAILED"


def test_fail_next_timeout_commits_then_withholds_response(
    bank: SignedClient, account: Account, settings: Any
) -> None:
    settings.SAFEPAY_BANK_TIMEOUT_SECONDS = 42
    assert body(arm(bank, count=1, reason="INJECTED_FAILURE", mode="timeout"))["mode"] == "timeout"
    req = transfer_request(account)
    seen: list[int] = []

    def fake_sleep(seconds: float) -> None:
        # by the time the response is withheld, the transfer is already committed and settled
        seen.append(Transfer.objects.filter(end_to_end_id=req["end_to_end_id"], status="SETTLED").count())
        assert seconds == 42

    with mock.patch("apps.transfers.api.sleep", side_effect=fake_sleep) as slept:
        bank.post("/v1/transfers", req)
    assert slept.call_count == 1
    assert seen == [1]
    # the caller retries with the same e2e id and gets the original transfer, no double debit
    with mock.patch("apps.transfers.api.sleep") as not_slept:
        retry = bank.post("/v1/transfers", req)
    assert retry.status_code == 200
    assert not_slept.call_count == 0
    assert balance(account) == account.balance_minor - req["amount_minor"]
