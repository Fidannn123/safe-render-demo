"""Signed statement reconciliation, ledger recomputation and automatic T4 freeze."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from apps.audit.chain import AuditChainError, append_event, verify_chain
from apps.bank_adapter.client import SignedBankClient
from apps.bank_adapter.models import Receipt
from apps.bank_adapter.signing import canonical
from apps.ledger.models import Balance, JournalEntry, Posting
from apps.ledger.service import balance_of
from apps.ledger.tenancy import tenant_transaction
from apps.payments.models import Payment
from apps.payments.service import lock_tenant_limits, transition
from apps.payments.types import Actor

from .models import Incident

RECONCILER = Actor("reconciler", "recon")


def incident(
    tenant_id: uuid.UUID,
    code: str,
    detail: dict[str, Any],
    *,
    payment_id: uuid.UUID | None = None,
    freeze: bool = False,
) -> Incident:
    digest = hashlib.sha256(
        canonical({"code": code, "payment_id": str(payment_id), "detail": detail})
    ).hexdigest()
    item, created = Incident.objects.get_or_create(
        tenant_id=tenant_id,
        fingerprint=digest,
        defaults={"code": code, "payment_id": payment_id, "detail": detail, "freezes_tenant": freeze},
    )
    if freeze:
        limits = lock_tenant_limits(tenant_id)
        if not limits.frozen:
            limits.frozen, limits.frozen_at = True, timezone.now()
            limits.save(update_fields=["frozen", "frozen_at", "updated_at"])
    if created:
        append_event(
            tenant_id=tenant_id,
            actor=RECONCILER.as_dict(),
            action="RECON_INCIDENT",
            subject_type="tenant",
            subject_id=str(tenant_id),
            data={"code": code, "incident_id": str(item.id), "freeze": freeze, **detail},
        )
    return item


def verify_ledger(tenant_id: uuid.UUID) -> bool:
    """Recompute every balance from immutable postings; also check entry and audit-chain invariants."""
    valid = True
    expected: dict[tuple[uuid.UUID, str], int] = {}
    for posting in Posting.objects.filter(tenant_id=tenant_id).select_related("account"):
        key = (posting.account_id, posting.currency)
        expected[key] = expected.get(key, 0) + posting.amount_minor * posting.account.normal_sign
    actual = {
        (row.account_id, row.currency): row.balance for row in Balance.objects.filter(tenant_id=tenant_id)
    }
    if expected != actual:
        incident(tenant_id, "AMOUNT_MISMATCH", {"component": "ledger_balance"}, freeze=True)
        valid = False
    sums = (
        Posting.objects.filter(tenant_id=tenant_id)
        .values("entry_id", "currency")
        .annotate(total=Sum("amount_minor"))
        .exclude(total=0)
    )
    empty = JournalEntry.objects.filter(tenant_id=tenant_id, postings__isnull=True).exists()
    if sums.exists() or empty:
        incident(tenant_id, "AMOUNT_MISMATCH", {"component": "journal_entries"}, freeze=True)
        valid = False
    try:
        verify_chain(tenant_id)
    except AuditChainError:
        incident(tenant_id, "AMOUNT_MISMATCH", {"component": "audit_chain"}, freeze=True)
        valid = False
    return valid


def reconcile_tenant(
    tenant_id: uuid.UUID, day: date | None = None, client: SignedBankClient | None = None
) -> dict[str, Any]:
    from apps.tenants.models import Tenant  # noqa: PLC0415

    day = day or timezone.now().date()
    with tenant_transaction(tenant_id):
        account_id = Tenant.objects.get(id=tenant_id).bank_account_id
    # No state changes until the bank signature and statement arithmetic have been verified.
    statement = (client or SignedBankClient()).statement(account_id, day)
    return reconcile_statement(tenant_id, statement)


def reconcile_statement(tenant_id: uuid.UUID, statement: dict[str, Any]) -> dict[str, Any]:
    """Consume a statement verified by SignedBankClient.statement (internal worker boundary)."""
    from apps.tenants.models import Tenant  # noqa: PLC0415

    matched = 0
    with tenant_transaction(tenant_id):
        lock_tenant_limits(tenant_id)
        if str(Tenant.objects.get(id=tenant_id).bank_account_id) != statement["account_id"]:
            raise ValueError("statement account does not belong to tenant")
        before = Incident.objects.filter(tenant_id=tenant_id).count()
        seen: set[str] = set()
        for line in statement["lines"]:
            if line["direction"] != "DEBIT":
                continue
            payment_id = uuid.UUID(line["end_to_end_id"])
            payment = Payment.objects.filter(id=payment_id, tenant_id=tenant_id).first()
            if str(payment_id) in seen:
                incident(
                    tenant_id,
                    "AMOUNT_MISMATCH",
                    {"component": "duplicate_statement_line"},
                    payment_id=payment_id,
                    freeze=True,
                )
                continue
            seen.add(str(payment_id))
            if payment is None:
                incident(
                    tenant_id,
                    "MISSING_IN_LEDGER",
                    {"date": statement["date"]},
                    payment_id=payment_id,
                    freeze=True,
                )
                continue
            receipt = Receipt.objects.filter(payment_id=payment_id, tenant_id=tenant_id).first()
            payee_mismatch = receipt is not None and receipt.document["creditor"] != line["counterparty"]
            if (
                payment.amount_minor != line["amount_minor"]
                or payment.currency != statement["currency"]
                or payment.state not in {"SUBMITTED", "SETTLED", "RECONCILED"}
                or payee_mismatch
            ):
                incident(
                    tenant_id,
                    "AMOUNT_MISMATCH",
                    {"date": statement["date"]},
                    payment_id=payment_id,
                    freeze=True,
                )
            elif payment.state == "SETTLED":
                transition(payment, "RECONCILED", "recon_matched", RECONCILER)
                matched += 1
        start = datetime.combine(date.fromisoformat(statement["date"]), time.min, tzinfo=UTC)
        for receipt in Receipt.objects.filter(
            tenant_id=tenant_id, verified_at__gte=start, verified_at__lt=start + timedelta(days=1)
        ):
            if (
                receipt.document["status"] == "SETTLED"
                and str(receipt.payment_id) not in seen
                and timezone.now() - receipt.verified_at > timedelta(hours=1)
            ):
                incident(
                    tenant_id, "MISSING_IN_BANK", {"date": statement["date"]}, payment_id=receipt.payment_id
                )
        valid = verify_ledger(tenant_id)
        # Today's closing balance includes every pending reservation. A bank timeout can already have
        # settled a SUBMITTED payment; defer the bank-total comparison until its receipt is recovered.
        if (
            statement["date"] == timezone.now().date().isoformat()
            and not Payment.objects.filter(tenant_id=tenant_id, state="SUBMITTED").exists()
        ):
            bank_total = sum(
                balance_of(tenant_id, code, statement["currency"])
                for code in ("Bank:available", "Bank:reserved", "Bank:in_transit")
            )
            if bank_total != statement["closing_balance_minor"]:
                incident(
                    tenant_id,
                    "AMOUNT_MISMATCH",
                    {
                        "component": "bank_closing_balance",
                        "ledger_minor": bank_total,
                        "bank_minor": statement["closing_balance_minor"],
                    },
                    freeze=True,
                )
                valid = False
        return {
            "matched": matched,
            "new_incidents": Incident.objects.filter(tenant_id=tenant_id).count() - before,
            "ledger_valid": valid,
            "frozen": lock_tenant_limits(tenant_id).frozen,
        }
