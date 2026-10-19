"""Payments: state row, tenant-limits lock row, transactional outbox, idempotency store.

State names come from spec/state_machine.yaml (apps.payments.spec); the CHECK on `state` is generated
from the spec by migration 0002. Partial UNIQUE(tenant_id, invoice_fingerprint) over live payments
(live = not DENIED, not FAILED, not REJECTED with fraud_or_invalid) forbids paying an invoice twice.
"""

from __future__ import annotations

import uuid

from django.db import models
from django.db.models import Q

REJECT_REASONS = ("fraud_or_invalid", "not_now")  # spec/openapi-core.yaml RejectReason
LIVE_PAYMENT = ~Q(state__in=["DENIED", "FAILED"]) & ~Q(state="REJECTED", reject_reason="fraud_or_invalid")


class Payment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)  # = end_to_end_id
    tenant_id = models.UUIDField()
    invoice_id = models.UUIDField()
    state = models.CharField(max_length=16)
    vendor_id = models.UUIDField(null=True, blank=True)
    account_id = models.UUIDField(null=True, blank=True)  # vendor REGISTRY account, never invoice text
    amount_minor = models.BigIntegerField(null=True, blank=True)
    currency = models.CharField(max_length=3, null=True, blank=True)  # noqa: DJ001 (API null)
    reference = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001 (API null)
    due_date = models.DateField(null=True, blank=True)
    requested_execution_date = models.DateField(null=True, blank=True)
    expense_account = models.CharField(max_length=16, default="Expense")
    invoice_fingerprint = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001 (API null)
    invoice_accrued = models.BooleanField(default=False)
    accrual_entry_id = models.UUIDField(null=True, blank=True)
    tier = models.CharField(max_length=2, null=True, blank=True)  # noqa: DJ001 (API null)
    decision_id = models.UUIDField(null=True, blank=True)
    initiated_by = models.JSONField(null=True, blank=True)
    approvals_required = models.SmallIntegerField(default=0)
    approval_warnings = models.JSONField(default=list)
    reject_reason = models.CharField(max_length=16, null=True, blank=True)  # noqa: DJ001 (API null)
    payee_reportable = models.BooleanField(default=False)
    cooloff_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_payment"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(
                fields=["tenant_id", "invoice_fingerprint"],
                condition=Q(invoice_fingerprint__isnull=False) & LIVE_PAYMENT,
                name="payments_live_fingerprint_uniq",
            ),
            models.CheckConstraint(
                condition=Q(reject_reason__isnull=True) | Q(reject_reason__in=REJECT_REASONS),
                name="payments_reject_reason_chk",
            ),
            models.CheckConstraint(
                condition=~Q(state="REJECTED") | Q(reject_reason__isnull=False),
                name="payments_rejected_has_reason_chk",
            ),
            models.CheckConstraint(
                condition=Q(amount_minor__isnull=True) | Q(amount_minor__gt=0), name="payments_amount_chk"
            ),
            models.CheckConstraint(
                condition=Q(currency__isnull=True) | Q(currency__regex=r"^[A-Z]{3}$"),
                name="payments_currency_chk",
            ),
            models.CheckConstraint(
                condition=Q(tier__isnull=True) | Q(tier__in=["T1", "T2", "T3"]), name="payments_tier_chk"
            ),
            models.CheckConstraint(
                condition=Q(approvals_required__gte=0) & Q(approvals_required__lte=2),
                name="payments_approvals_required_chk",
            ),
        ]
        indexes = [models.Index(fields=["tenant_id", "state"], name="payments_tenant_state_idx")]  # noqa: RUF012

    def __str__(self) -> str:
        return f"payment {self.id} {self.state}"


class TenantLimits(models.Model):
    """One row per tenant: the lock row (SELECT ... FOR UPDATE) that serialises caps and reserves."""

    tenant_id = models.UUIDField(primary_key=True)
    frozen = models.BooleanField(default=False)
    frozen_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "payments_tenant_limits"

    def __str__(self) -> str:
        return f"limits {self.tenant_id}"


class OutboxMessage(models.Model):
    """Transactional outbox row, written in the same transaction as the state change.

    RLS-exempt (settings SAFEPAY_RLS_EXEMPT_TABLES) so the worker can claim rows across tenants with
    SELECT ... FOR UPDATE SKIP LOCKED; the payload carries ids only, never amounts or account numbers.
    """

    STATUS_PENDING = "PENDING"
    STATUS_DELIVERED = "DELIVERED"
    STATUS_DEAD = "DEAD"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    payment_id = models.UUIDField()
    kind = models.CharField(max_length=32)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=16, default=STATUS_PENDING)
    attempts = models.IntegerField(default=0)
    available_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True, default="")

    class Meta:
        db_table = "payments_outbox"
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                condition=Q(status__in=["PENDING", "DELIVERED", "DEAD"]), name="payments_outbox_status_chk"
            ),
        ]
        indexes = [models.Index(fields=["status", "available_at"], name="payments_outbox_due_idx")]  # noqa: RUF012

    def __str__(self) -> str:
        return f"outbox {self.kind} {self.payment_id} {self.status}"


class IdempotencyKey(models.Model):
    """API idempotency store: UNIQUE(tenant_id, key), request SHA-256 and the stored response."""

    STATUS_IN_PROGRESS = "IN_PROGRESS"
    STATUS_COMPLETED = "COMPLETED"

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField()
    key = models.CharField(max_length=128)
    request_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default=STATUS_IN_PROGRESS)
    response_status = models.IntegerField(null=True, blank=True)
    response_body = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payments_idempotency_key"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["tenant_id", "key"], name="payments_idem_tenant_key_uniq"),
            models.CheckConstraint(
                condition=Q(request_sha256__regex=r"^[0-9a-f]{64}$"), name="payments_idem_sha_chk"
            ),
        ]

    def __str__(self) -> str:
        return f"idempotency {self.key}"
