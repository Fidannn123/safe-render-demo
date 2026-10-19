"""Read-model inputs for deterministic forecasts and financial analysis."""

from __future__ import annotations

import uuid

from django.db import models


class Receivable(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    customer_id = models.UUIDField(default=uuid.uuid4)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="USD")
    issued_on = models.DateField()
    due_on = models.DateField()
    paid_on = models.DateField(null=True)

    class Meta:
        db_table = "cashflow_receivable"

    def __str__(self) -> str:
        return f"Receivable {self.id} {self.amount_minor} {self.currency}"


class RecurringOpex(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    name = models.CharField(max_length=140)
    amount_minor = models.PositiveBigIntegerField()
    currency = models.CharField(max_length=3, default="USD")
    next_due = models.DateField()
    interval_days = models.PositiveIntegerField(default=7)

    class Meta:
        db_table = "cashflow_recurring_opex"
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                condition=models.Q(interval_days__gt=0), name="cashflow_interval_positive"
            ),
        ]

    def __str__(self) -> str:
        return self.name
