# ruff: noqa: PT012
"""Ledger invariants enforced by PostgreSQL (deferred triggers fire at COMMIT: transaction=True)."""

from __future__ import annotations

import uuid

import pytest
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.ledger.models import Account, Balance, JournalEntry, Posting
from apps.ledger.service import LedgerError, balance_of, ensure_account, ensure_tenant_accounts, post_template
from apps.ledger.spec import ledger_spec
from apps.ledger.tenancy import TenantMismatchError, bind_tenant, tenant_transaction

pytestmark = pytest.mark.django_db(transaction=True)
USD = "USD"


@pytest.fixture
def tenant() -> uuid.UUID:
    t = uuid.uuid4()
    with tenant_transaction(t):
        post_template(t, "opening_balance", 10_000, USD)
    return t


def _bal(tenant: uuid.UUID, code: str, ccy: str = USD) -> int:
    with tenant_transaction(tenant):
        return balance_of(tenant, code, ccy)


def test_opening_balance(tenant: uuid.UUID) -> None:
    assert _bal(tenant, "Bank:available") == 10_000
    assert _bal(tenant, "Equity:opening") == 10_000  # credit-normal: shown positive


def test_fixed_accounts_from_spec(tenant: uuid.UUID) -> None:
    with tenant_transaction(tenant):
        codes = {a.code for a in ensure_tenant_accounts(tenant)}
        assert codes == set(ledger_spec().fixed_accounts)
        ap = ensure_account(tenant, f"AP:{uuid.uuid4()}")
        assert (ap.type, ap.normal_sign, ap.non_negative) == ("liability", -1, False)
        bank = Account.objects.get(tenant_id=tenant, code="Bank:in_transit")
        assert bank.non_negative
        assert bank.normal_sign == 1


def test_unbalanced_entry_rejected_at_commit(tenant: uuid.UUID) -> None:
    with pytest.raises(IntegrityError, match="does not balance"), tenant_transaction(tenant):
        acct = Account.objects.get(tenant_id=tenant, code="Equity:opening")
        entry = JournalEntry.objects.create(tenant_id=tenant, template="manual")
        Posting.objects.create(tenant_id=tenant, entry=entry, account=acct, amount_minor=-5, currency=USD)
        exp = ensure_account(tenant, "Expense")
        Posting.objects.create(tenant_id=tenant, entry=entry, account=exp, amount_minor=4, currency=USD)
    with tenant_transaction(tenant):
        assert not JournalEntry.objects.filter(template="manual").exists()


def test_per_currency_balance_required(tenant: uuid.UUID) -> None:
    with pytest.raises(IntegrityError, match="does not balance"), tenant_transaction(tenant):
        entry = JournalEntry.objects.create(tenant_id=tenant, template="manual")
        exp = ensure_account(tenant, "Expense")
        eq = ensure_account(tenant, "Equity:opening")
        Posting.objects.create(tenant_id=tenant, entry=entry, account=exp, amount_minor=5, currency="USD")
        Posting.objects.create(tenant_id=tenant, entry=entry, account=eq, amount_minor=-5, currency="EUR")


def test_entry_without_postings_rejected(tenant: uuid.UUID) -> None:
    with pytest.raises(IntegrityError, match="at least 2"), tenant_transaction(tenant):
        JournalEntry.objects.create(tenant_id=tenant, template="manual")


def test_overdraft_rejected_immediately(tenant: uuid.UUID) -> None:
    with pytest.raises(IntegrityError, match="ledger_balance_non_negative_chk"), tenant_transaction(tenant):
        post_template(tenant, "reserve", 10_001, USD)
    assert _bal(tenant, "Bank:available") == 10_000
    assert _bal(tenant, "Bank:reserved") == 0


@pytest.mark.parametrize("code", ["Bank:reserved", "Bank:in_transit"])
def test_other_bank_accounts_cannot_go_negative(tenant: uuid.UUID, code: str) -> None:
    template = "submit" if code == "Bank:reserved" else "release_in_transit"
    with pytest.raises(IntegrityError, match="ledger_balance_non_negative_chk"), tenant_transaction(tenant):
        post_template(tenant, template, 1, USD)


def test_balance_rows_only_written_by_trigger(tenant: uuid.UUID) -> None:
    for sql in (
        "UPDATE ledger_balance SET balance = balance + 1000000",
        "DELETE FROM ledger_balance",
        "INSERT INTO ledger_balance (tenant_id, account_id, currency, balance, non_negative, updated_at) "
        "SELECT tenant_id, account_id, 'EUR', 1, false, now() FROM ledger_balance LIMIT 1",
    ):
        with (
            pytest.raises(DatabaseError, match="ledger_balance"),
            tenant_transaction(tenant),
            connection.cursor() as cur,
        ):
            cur.execute(sql)
    assert _bal(tenant, "Bank:available") == 10_000


def test_accounts_are_immutable(tenant: uuid.UUID) -> None:
    with pytest.raises(DatabaseError, match="ledger_account"), tenant_transaction(tenant):
        Account.objects.filter(tenant_id=tenant, code="Bank:available").update(non_negative=False)


def test_posting_to_other_tenants_account_rejected(tenant: uuid.UUID) -> None:
    other = uuid.uuid4()
    with tenant_transaction(other):
        foreign = ensure_account(other, "Expense")
    with pytest.raises(IntegrityError, match="not in tenant"), tenant_transaction(tenant):
        entry = JournalEntry.objects.create(tenant_id=tenant, template="manual")
        Posting.objects.create(tenant_id=tenant, entry=entry, account=foreign, amount_minor=5, currency=USD)


def test_reversal_is_a_new_entry(tenant: uuid.UUID) -> None:
    vendor = uuid.uuid4()
    with tenant_transaction(tenant):
        acc = post_template(tenant, "invoice_accepted", 700, USD, vendor_id=vendor, expense_account="COGS")
        rev = post_template(
            tenant, "invoice_voided", 700, USD, vendor_id=vendor, expense_account="COGS", reverses=acc
        )
    assert rev.id != acc.id
    assert _bal(tenant, "COGS") == 0
    assert _bal(tenant, f"AP:{vendor}") == 0
    with tenant_transaction(tenant):
        assert Posting.objects.filter(entry=acc).count() == 2
        assert Posting.objects.filter(entry=rev).count() == 2


@pytest.mark.parametrize(
    ("template", "kwargs"),
    [
        ("no_such_template", {}),
        ("invoice_accepted", {"expense_account": "Expense"}),  # missing vendor_id
        ("invoice_accepted", {"vendor_id": uuid.uuid4(), "expense_account": "Bank:available"}),
        ("ar_invoice_issued", {}),  # missing customer_id
    ],
)
def test_bad_template_arguments(tenant: uuid.UUID, template: str, kwargs: dict[str, object]) -> None:
    with pytest.raises(LedgerError), tenant_transaction(tenant):
        post_template(tenant, template, 100, USD, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(("amount", "ccy"), [(0, USD), (-1, USD), (True, USD), (100, "usd"), (100, "US")])
def test_bad_money_rejected(tenant: uuid.UUID, amount: int, ccy: str) -> None:
    with pytest.raises(LedgerError), tenant_transaction(tenant):
        post_template(tenant, "opening_balance", amount, ccy)


def test_post_requires_transaction(tenant: uuid.UUID) -> None:
    with pytest.raises(RuntimeError):
        post_template(tenant, "opening_balance", 1, USD)


def test_cannot_switch_tenant_mid_transaction(tenant: uuid.UUID) -> None:
    with pytest.raises(TenantMismatchError), transaction.atomic():
        bind_tenant(tenant)
        bind_tenant(uuid.uuid4())


def test_rls_hides_balances_without_tenant(tenant: uuid.UUID) -> None:
    with transaction.atomic():
        assert Balance.objects.count() == 0
        assert Posting.objects.count() == 0
