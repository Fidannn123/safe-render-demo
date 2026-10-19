"""SIMULATED transfers, signed receipts (append-only) and the failure-injection switch."""

from __future__ import annotations

import uuid

from django.db import models

from apps.accounts.models import Account


class Transfer(models.Model):
    """One credit transfer; end_to_end_id (core payment_id) is the idempotency key."""

    class Status(models.TextChoices):
        ACCEPTED = "ACCEPTED"
        SETTLED = "SETTLED"
        FAILED = "FAILED"

    transfer_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    end_to_end_id = models.UUIDField(unique=True)
    request_sha256 = models.CharField(max_length=64)
    request = models.JSONField()
    debtor = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="transfers")
    status = models.CharField(max_length=8, choices=Status.choices)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "transfers_transfer"

    def __str__(self) -> str:
        return f"{self.end_to_end_id} {self.status}"


class Receipt(models.Model):
    """Ed25519-signed receipt (spec/schemas/Receipt.json). APPEND-ONLY (db_policy trigger)."""

    receipt_id = models.UUIDField(primary_key=True)
    transfer = models.OneToOneField(Transfer, on_delete=models.PROTECT, related_name="receipt")
    document = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "transfers_receipt"

    def __str__(self) -> str:
        return str(self.receipt_id)


class FailureArm(models.Model):
    """SIMULATION ONLY. Singleton row (id=1) arming failure injection for the next N new transfers."""

    class Reason(models.TextChoices):
        INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
        CREDITOR_ACCOUNT_INVALID = "CREDITOR_ACCOUNT_INVALID"
        INJECTED_FAILURE = "INJECTED_FAILURE"

    class Mode(models.TextChoices):
        FAIL = "fail"
        TIMEOUT = "timeout"

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    remaining = models.PositiveIntegerField(default=0)
    reason = models.CharField(max_length=32, choices=Reason.choices, default=Reason.INJECTED_FAILURE)
    mode = models.CharField(max_length=8, choices=Mode.choices, default=Mode.FAIL)

    class Meta:
        db_table = "transfers_failurearm"
        constraints = [  # noqa: RUF012
            models.CheckConstraint(condition=models.Q(id=1), name="failurearm_singleton")
        ]

    def __str__(self) -> str:
        return f"{self.remaining} x {self.mode}/{self.reason}"
