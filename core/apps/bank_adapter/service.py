"""Recoverable bank delivery: commit request binding before I/O; retry the same bank idempotency id."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

import yaml
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.audit.chain import append_event
from apps.ledger.tenancy import bind_tenant
from apps.payments.errors import ProblemError
from apps.payments.models import OutboxMessage, Payment
from apps.payments.service import lock_tenant_limits, transition
from apps.payments.types import SYSTEM, Actor, TransitionContext

from .client import SignedBankClient
from .models import Receipt, TransferSubmission
from .signing import RemoteError


def transfer_request(payment: Payment) -> dict[str, Any]:
    """Resolve bank details ONLY from the registry, and bind them before the first network attempt."""
    from apps.tenants.models import Tenant  # noqa: PLC0415
    from apps.vendors.models import BankAccount  # noqa: PLC0415

    if payment.account_id is None or payment.vendor_id is None:
        raise RemoteError("PAYMENT_NOT_DELIVERABLE")
    account = BankAccount.objects.select_related("vendor").get(
        tenant_id=payment.tenant_id, id=payment.account_id, vendor_id=payment.vendor_id, active=True
    )
    tenant = Tenant.objects.get(id=payment.tenant_id)
    return {
        "end_to_end_id": str(payment.id),
        "debtor_account_id": str(tenant.bank_account_id),
        "creditor": {"country": account.country, "iban": account.iban, "name": account.vendor.name},
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "remittance_info": payment.reference or "",
    }


def _prepare(message: OutboxMessage, now: datetime) -> bool:
    limits = lock_tenant_limits(message.tenant_id)
    payment = Payment.objects.select_for_update().get(id=message.payment_id, tenant_id=message.tenant_id)
    if payment.state in {"FAILED", "DENIED", "REJECTED", "SETTLED", "RECONCILED"}:
        _delivered(message, now)
        return False
    if limits.frozen:
        message.available_at = now + timedelta(seconds=30)
        message.save(update_fields=["available_at"])
        return False
    if payment.state == "RESERVED":
        if payment.tier == "T2" and payment.cooloff_until and payment.cooloff_until > now:
            message.available_at = payment.cooloff_until
            message.save(update_fields=["available_at"])
            return False
        submission = TransferSubmission(payment_id=payment.id, tenant_id=payment.tenant_id)
        submission.set_request(transfer_request(payment))
        submission.save(force_insert=True)
        transition(payment, "SUBMITTED", "outbox_delivered", SYSTEM, TransitionContext(now=now))
    elif payment.state != "SUBMITTED":
        raise RemoteError("PAYMENT_NOT_DELIVERABLE")
    return True


def _error_code(exc: Exception) -> str:
    """Typed codes only (never a remote body or account number) are persisted on the outbox row."""
    if isinstance(exc, (RemoteError, ProblemError)):
        return exc.code
    return type(exc).__name__


def _delivered(message: OutboxMessage, now: datetime) -> None:
    message.status, message.delivered_at, message.last_error = OutboxMessage.STATUS_DELIVERED, now, ""
    message.save(update_fields=["status", "delivered_at", "last_error"])


def _retry(message: OutboxMessage, code: str, now: datetime) -> None:
    policy = yaml.safe_load((settings.SAFEPAY_SPEC_DIR / "policy.yaml").read_text("utf-8"))["outbox"]
    message.attempts += 1
    message.last_error = code[:80]  # never persist a remote error body / IBAN
    if message.attempts >= int(policy["outbox_max_retries"]):
        message.status = OutboxMessage.STATUS_DEAD
        append_event(
            tenant_id=message.tenant_id,
            actor=SYSTEM.as_dict(),
            action="OUTBOX_EXHAUSTED",
            subject_type="payment",
            subject_id=str(message.payment_id),
            data={"outbox_id": str(message.id), "code": code, "attempts": message.attempts},
        )
    else:
        ceiling = min(
            int(policy["backoff_max_seconds"]), int(policy["backoff_base_seconds"]) * 2**message.attempts
        )
        message.available_at = now + timedelta(milliseconds=secrets.randbelow(ceiling * 1000 + 1))
    message.save(update_fields=["attempts", "last_error", "status", "available_at"])


def _deliver(message: OutboxMessage, client: SignedBankClient, now: datetime) -> None:
    limits = lock_tenant_limits(message.tenant_id)
    if limits.frozen:
        message.available_at = now + timedelta(seconds=30)
        message.save(update_fields=["available_at"])
        return
    payment = Payment.objects.select_for_update().get(id=message.payment_id, tenant_id=message.tenant_id)
    if payment.state in {"SETTLED", "RECONCILED", "FAILED"}:
        _delivered(message, now)
        return
    submission = TransferSubmission.objects.get(payment_id=payment.id, tenant_id=message.tenant_id)
    result = client.submit(submission.request)
    # Verify again at the authority boundary; injected transports cannot bypass receipt verification.
    client.verify_transfer(result, submission.request)
    document = result["receipt"]
    receipt = Receipt(id=document["receipt_id"], tenant_id=payment.tenant_id, payment_id=payment.id)
    receipt.set_document(document)
    receipt.save(force_insert=True)
    settled = document["status"] == "SETTLED"
    transition(
        payment,
        "SETTLED" if settled else "FAILED",
        "bank_receipt_settled" if settled else "bank_receipt_failed",
        Actor("bank", "bank-sandbox"),
        TransitionContext(
            now=now,
            attested=frozenset({"receipt_signature_valid"}),
            audit_data={"receipt_id": str(receipt.id)},
        ),
    )
    _delivered(message, now)


def process_outbox(
    batch_size: int = 25, client: SignedBankClient | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Claim cross-tenant rows with SKIP LOCKED; each row gets its own RLS transaction.

    Preparation commits SUBMITTED + encrypted request before the HTTP side effect. A crash after
    settlement replays that exact request with the same end_to_end_id; the bank debits once.
    """
    now = now or timezone.now()
    stats = {"processed": 0, "delivered": 0, "retried": 0}
    visited: set[Any] = set()
    for _ in range(batch_size):
        ready = False
        with transaction.atomic():
            message = (
                OutboxMessage.objects.select_for_update(skip_locked=True)
                .filter(status=OutboxMessage.STATUS_PENDING, available_at__lte=now)
                .exclude(id__in=visited)
                .order_by("available_at", "id")
                .first()
            )
            if message is None:
                break
            visited.add(message.id)
            stats["processed"] += 1
            bind_tenant(message.tenant_id)
            try:
                with transaction.atomic():
                    if message.kind == "invoice.extract":
                        from apps.agent.service import extract_invoice  # noqa: PLC0415

                        extract_invoice(message.payload["invoice_id"], message.tenant_id)
                        _delivered(message, now)
                    elif message.kind == "submit_transfer":
                        ready = _prepare(message, now)
                    else:
                        raise RemoteError("OUTBOX_KIND_UNKNOWN")
            except Exception as exc:  # worker survives one poisoned row; persisted errors are sanitized
                message.refresh_from_db()
                _retry(message, _error_code(exc), now)
        if ready:
            with transaction.atomic():
                message = OutboxMessage.objects.select_for_update().get(id=message.id)
                bind_tenant(message.tenant_id)
                if message.status != OutboxMessage.STATUS_PENDING:
                    continue
                try:
                    with transaction.atomic():
                        _deliver(message, client or SignedBankClient(), now)
                except Exception as exc:
                    message.refresh_from_db()
                    _retry(message, _error_code(exc), now)
        if message.status == OutboxMessage.STATUS_DELIVERED:
            stats["delivered"] += 1
        elif message.attempts:
            stats["retried"] += 1
    return stats
