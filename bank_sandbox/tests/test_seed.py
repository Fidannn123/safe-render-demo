"""seed_bank_accounts: SYNTHETIC, deterministic, idempotent; opening balance booked as a funding line."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command

from apps.accounts.iban import iban_valid, make_iban
from apps.accounts.models import Account, Entry

pytestmark = pytest.mark.django_db


def test_seed_is_synthetic_deterministic_and_idempotent() -> None:
    out = StringIO()
    call_command("seed_bank_accounts", "--seed", "42", stdout=out)
    first = {a.account_id: (a.iban, a.balance_minor) for a in Account.objects.all()}
    call_command("seed_bank_accounts", "--seed", "42", stdout=StringIO())
    assert {a.account_id: (a.iban, a.balance_minor) for a in Account.objects.all()} == first
    assert len(first) == 8
    for account in Account.objects.all():
        assert "(SYNTHETIC)" in account.owner_name
        assert iban_valid(account.iban)
        entry = Entry.objects.get(account=account)
        assert entry.direction == "CREDIT"
        assert entry.amount_minor == account.balance_minor
    assert "SIMULATED" in out.getvalue()


def test_seed_from_file(tmp_path: Path) -> None:
    spec = [
        {
            "account_id": "33333333-4444-4555-8666-777777777777",
            "iban": make_iban("GB", "SPSB00000000001234"),
            "owner_name": "Twin retail (SYNTHETIC)",
            "opening_balance_minor": 5_000_000,
        }
    ]
    path = tmp_path / "accounts.json"
    path.write_text(json.dumps(spec), encoding="utf-8")
    call_command("seed_bank_accounts", "--file", str(path), stdout=StringIO())
    account = Account.objects.get(pk=str(spec[0]["account_id"]))
    assert account.balance_minor == 5_000_000
    assert account.currency == "USD"


def test_iban_helpers() -> None:
    assert iban_valid("GB82WEST12345698765432")
    assert not iban_valid("GB83WEST12345698765432")
    assert not iban_valid("gb82west12345698765432")
    assert iban_valid(make_iban("DE", "370400440532013000"))
    assert make_iban("DE", "370400440532013000") == "DE89370400440532013000"
