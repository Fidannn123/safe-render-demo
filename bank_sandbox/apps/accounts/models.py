"""SIMULATED bank accounts and booked entries. No tenant_id: the bank has no SafePay tenants."""

from __future__ import annotations

import uuid

from django.db import models


class Account(models.Model):
    """A simulated account. balance_minor is updated in the same transaction as its entries."""

    account_id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    iban = models.CharField(max_length=34, unique=True)
    currency = models.CharField(max_length=3)
    owner_name = models.CharField(max_length=140)
    balance_minor = models.BigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_account"
        constraints = [  # noqa: RUF012 (Django Meta)
            models.CheckConstraint(
                condition=models.Q(balance_minor__gte=0), name="account_balance_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"), name="account_currency_iso"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.iban} ({self.owner_name})"


class Entry(models.Model):
    """A booked (settled) line on one account; the source of statements. Never updated."""

    class Direction(models.TextChoices):
        DEBIT = "DEBIT"
        CREDIT = "CREDIT"

    seq = models.BigAutoField(primary_key=True)
    line_id = models.UUIDField(unique=True, default=uuid.uuid4)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    transfer_id = models.UUIDField()
    end_to_end_id = models.UUIDField()
    direction = models.CharField(max_length=6, choices=Direction.choices)
    amount_minor = models.BigIntegerField()
    counterparty = models.JSONField()
    remittance_info = models.CharField(max_length=140, blank=True, default="")
    booked_at = models.DateTimeField()

    class Meta:
        db_table = "accounts_entry"
        indexes = [models.Index(fields=["account", "booked_at"])]  # noqa: RUF012
        constraints = [  # noqa: RUF012 (Django Meta)
            models.CheckConstraint(condition=models.Q(amount_minor__gt=0), name="entry_amount_positive"),
            models.UniqueConstraint(
                fields=["account", "end_to_end_id", "direction"], name="entry_unique_per_e2e"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.direction} {self.amount_minor} {self.end_to_end_id}"
