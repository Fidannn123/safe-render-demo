"""GET /v1/statements: signed daily statement, closing = opening + credits - debits."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

import pytest

from apps.accounts.models import Account
from apps.accounts.services import deposit
from apps.statements.models import Statement
from safepay_bank.signing import verify_document
from tests.conftest import Keys, SignedClient, new_account, transfer_request

pytestmark = pytest.mark.django_db

DAY1 = datetime(2026, 10, 17, 9, 0, tzinfo=UTC)
DAY2 = DAY1 + timedelta(days=1)
TODAY = DAY1 + timedelta(days=2)


def at(moment: datetime) -> Any:
    return mock.patch("safepay_bank.clock.now", return_value=moment)


def statement(bank: SignedClient, account: Account, day: str) -> Any:
    return bank.get("/v1/statements", f"account_id={account.account_id}&date={day}")


def check(data: dict[str, Any], keys: Keys, validate: Any) -> None:
    validate("Statement.json", data)
    assert verify_document(data, keys.bank_public)
    credits = sum(line["amount_minor"] for line in data["lines"] if line["direction"] == "CREDIT")
    debits = sum(line["amount_minor"] for line in data["lines"] if line["direction"] == "DEBIT")
    assert data["closing_balance_minor"] == data["opening_balance_minor"] + credits - debits


def test_daily_statements_chain_and_verify(bank: SignedClient, keys: Keys, validate: Any) -> None:
    with at(DAY1):
        acct = new_account(balance=100_000)
        bank.post("/v1/transfers", transfer_request(acct, 30_000), ts=int(DAY1.timestamp()))
    with at(DAY2):
        bank.post("/v1/transfers", transfer_request(acct, 20_000), ts=int(DAY2.timestamp()))
        deposit(acct, 5_000, end_to_end_id=uuid.uuid4(), remittance_info="Customer receipt (SYNTHETIC)")
        bank.post("/v1/transfers", transfer_request(acct, 10_000_000), ts=int(DAY2.timestamp()))  # FAILED
    with at(TODAY):
        s1 = json.loads(statement(bank, acct, "2026-10-17").content)
        s2 = json.loads(statement(bank, acct, "2026-10-18").content)
        s3 = json.loads(statement(bank, acct, "2026-10-19").content)
    for s in (s1, s2, s3):
        check(s, keys, validate)
    assert (s1["opening_balance_minor"], s1["closing_balance_minor"]) == (0, 70_000)
    assert [ln["direction"] for ln in s1["lines"]] == ["CREDIT", "DEBIT"]  # opening funding, payment
    assert (s2["opening_balance_minor"], s2["closing_balance_minor"]) == (70_000, 55_000)
    assert len(s2["lines"]) == 2  # the FAILED transfer is not booked
    assert s3["lines"] == []
    assert s3["opening_balance_minor"] == s3["closing_balance_minor"] == 55_000
    assert Account.objects.get(pk=acct.pk).balance_minor == 55_000
    assert s2["lines"][0]["remittance_info"] == "INV-1042"


def test_closed_day_is_issued_once(bank: SignedClient, keys: Keys) -> None:
    with at(DAY1):
        acct = new_account(balance=100_000)
    with at(TODAY):
        first = json.loads(statement(bank, acct, "2026-10-17").content)
        second = json.loads(statement(bank, acct, "2026-10-17").content)
        today_a = json.loads(statement(bank, acct, "2026-10-19").content)
        today_b = json.loads(statement(bank, acct, "2026-10-19").content)
    assert first == second
    assert today_a["statement_id"] != today_b["statement_id"]  # interim snapshots of an open day
    assert Statement.objects.filter(account=acct, date="2026-10-17", final=True).count() == 1


def test_statement_errors(bank: SignedClient, account: Account, validate: Any) -> None:
    missing = bank.get("/v1/statements", f"account_id={uuid.uuid4()}&date=2026-10-17")
    assert missing.status_code == 404
    assert json.loads(missing.content)["code"] == "ACCOUNT_NOT_FOUND"
    future = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    for query in (
        f"account_id={account.account_id}&date={future}",
        f"account_id={account.account_id}&date=2026-13-40",
        f"account_id={account.account_id}",
        "date=2026-10-17",
        "account_id=nope&date=2026-10-17",
    ):
        response = bank.get("/v1/statements", query)
        assert response.status_code == 422, query
        assert json.loads(response.content)["code"] == "VALIDATION_FAILED"
        validate("Problem.json", json.loads(response.content))


def test_tampered_statement_fails_verification(bank: SignedClient, account: Account, keys: Keys) -> None:
    data = json.loads(statement(bank, account, datetime.now(UTC).date().isoformat()).content)
    assert verify_document(data, keys.bank_public)
    assert not verify_document(
        {**data, "closing_balance_minor": data["closing_balance_minor"] + 1}, keys.bank_public
    )
