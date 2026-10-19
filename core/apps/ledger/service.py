"""Ledger posting API. Postings come from spec templates only; a reversal is always a NEW journal entry.

Balances are maintained by the database (trigger on ledger_posting), never written from Python.
"""

from __future__ import annotations

import re
import uuid

from django.db import connection

from .models import Account, Balance, JournalEntry, Posting
from .spec import CUSTOMER_PLACEHOLDER, EXPENSE_PLACEHOLDER, VENDOR_PLACEHOLDER, ledger_spec
from .tenancy import bind_tenant

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class LedgerError(ValueError):
    """Invalid posting request (unknown template, missing account argument, bad amount)."""


def validate_money(amount_minor: int, currency: str) -> None:
    if type(amount_minor) is not int or amount_minor <= 0:
        raise LedgerError("amount_minor must be a positive int (minor units)")
    if not CURRENCY_RE.match(currency):
        raise LedgerError("currency must be an ISO-4217 alphabetic code")


def ensure_account(tenant_id: uuid.UUID, code: str) -> Account:
    """Get or create (race-safe, INSERT ... ON CONFLICT DO NOTHING) a tenant account from the spec."""
    spec = ledger_spec().account_for_code(code)
    Account.objects.bulk_create(
        [
            Account(
                tenant_id=tenant_id,
                code=code,
                spec_key=spec.key,
                type=spec.type,
                normal_sign=spec.normal_sign,
                non_negative=spec.non_negative,
            )
        ],
        ignore_conflicts=True,
    )
    return Account.objects.get(tenant_id=tenant_id, code=code)


def ensure_tenant_accounts(tenant_id: uuid.UUID) -> list[Account]:
    """Create the fixed (non-parametric) accounts of a tenant."""
    return [ensure_account(tenant_id, code) for code in ledger_spec().fixed_accounts]


def resolve_account_code(
    template_account: str,
    *,
    vendor_id: uuid.UUID | None,
    customer_id: uuid.UUID | None,
    expense_account: str | None,
) -> str:
    spec = ledger_spec()
    if template_account == EXPENSE_PLACEHOLDER:
        if expense_account not in spec.expense_accounts:
            raise LedgerError(f"expense_account must be one of {spec.expense_accounts}")
        return str(expense_account)
    if VENDOR_PLACEHOLDER in template_account:
        if vendor_id is None:
            raise LedgerError(f"{template_account} needs vendor_id")
        return template_account.replace(VENDOR_PLACEHOLDER, str(vendor_id))
    if CUSTOMER_PLACEHOLDER in template_account:
        if customer_id is None:
            raise LedgerError(f"{template_account} needs customer_id")
        return template_account.replace(CUSTOMER_PLACEHOLDER, str(customer_id))
    if "<" in template_account:
        raise LedgerError(f"unsupported placeholder in {template_account}")
    return template_account


def post_template(
    tenant_id: uuid.UUID,
    template: str,
    amount_minor: int,
    currency: str,
    *,
    vendor_id: uuid.UUID | None = None,
    customer_id: uuid.UUID | None = None,
    expense_account: str | None = None,
    payment_id: uuid.UUID | None = None,
    reverses: JournalEntry | None = None,
    memo: str = "",
) -> JournalEntry:
    """Insert one balanced journal entry from a spec template. Must run inside transaction.atomic().

    A CHECK violation (e.g. Bank:available < 0) surfaces as django.db.IntegrityError at INSERT time.
    """
    if not connection.in_atomic_block:
        raise RuntimeError("post_template must run inside transaction.atomic()")
    tpl = ledger_spec().templates.get(template)
    if tpl is None:
        raise LedgerError(f"unknown posting template {template!r}")
    validate_money(amount_minor, currency)
    bind_tenant(tenant_id)
    entry = JournalEntry.objects.create(
        tenant_id=tenant_id, template=template, payment_id=payment_id, reverses=reverses, memo=memo[:200]
    )
    for line in tpl.lines:
        code = resolve_account_code(
            line.account, vendor_id=vendor_id, customer_id=customer_id, expense_account=expense_account
        )
        account = ensure_account(tenant_id, code)
        # One INSERT per line so the balance trigger and CHECK fire in template order.
        Posting.objects.create(
            tenant_id=tenant_id,
            entry=entry,
            account=account,
            amount_minor=line.sign * amount_minor,
            currency=currency,
        )
    return entry


def balance_of(tenant_id: uuid.UUID, code: str, currency: str) -> int:
    """Balance on the account's normal side (0 if the account or balance row does not exist)."""
    row = (
        Balance.objects.filter(tenant_id=tenant_id, account__code=code, currency=currency)
        .values_list("balance", flat=True)
        .first()
    )
    return int(row) if row is not None else 0
