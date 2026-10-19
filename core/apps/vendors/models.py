"""Verified vendor registry: invoice text can never set the account used for payment."""

from __future__ import annotations

import base64
import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from django.db import models
from django.utils import timezone

from apps.accounts.secrets import random_key


def encrypt_iban(iban: str, tenant_id: uuid.UUID) -> str:
    nonce = os.urandom(12)
    encrypted = AESGCM(random_key("pii_aes256_key")).encrypt(nonce, iban.encode(), str(tenant_id).encode())
    return base64.b64encode(nonce + encrypted).decode("ascii")


def decrypt_iban(value: str, tenant_id: uuid.UUID) -> str:
    data = base64.b64decode(value)
    return (
        AESGCM(random_key("pii_aes256_key")).decrypt(data[:12], data[12:], str(tenant_id).encode()).decode()
    )


class Vendor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    name = models.CharField(max_length=140)
    tax_id = models.CharField(max_length=64, blank=True)
    blocked = models.BooleanField(default=False)
    expense_account = models.CharField(max_length=16, default="Expense")
    criticality = models.CharField(max_length=6, default="MEDIUM")
    late_fee_bp = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "vendors_vendor"

    def __str__(self) -> str:
        return self.name

    @property
    def bank_account(self) -> BankAccount | None:
        return BankAccount.objects.filter(tenant_id=self.tenant_id, vendor=self, active=True).first()


class BankAccount(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT)
    country = models.CharField(max_length=2)
    iban_encrypted = models.TextField()
    holder_name = models.CharField(max_length=140)
    active = models.BooleanField(default=True)
    verified_at = models.DateTimeField(default=timezone.now)
    changed_at = models.DateTimeField(default=timezone.now)
    first_used_at = models.DateTimeField(null=True)

    class Meta:
        db_table = "vendors_bank_account"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(
                fields=["tenant_id", "vendor"],
                condition=models.Q(active=True),
                name="vendors_one_active_account",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.holder_name} ({self.country})"  # never the IBAN

    @property
    def iban(self) -> str:
        return decrypt_iban(self.iban_encrypted, self.tenant_id)

    @iban.setter
    def iban(self, value: str) -> None:
        self.iban_encrypted = encrypt_iban(value, self.tenant_id)


class BankAccountChange(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    vendor = models.ForeignKey(Vendor, on_delete=models.PROTECT)
    status = models.CharField(max_length=24, default="PENDING_CONFIRMATION")
    requested_by = models.UUIDField()
    confirmed_by = models.UUIDField(null=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True)
    country = models.CharField(max_length=2)
    iban_encrypted = models.TextField()
    holder_name = models.CharField(max_length=140)
    request_attestation = models.JSONField()
    confirm_attestation = models.JSONField(null=True)
    reason = models.CharField(max_length=500)

    class Meta:
        db_table = "vendors_bank_account_change"
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                condition=~models.Q(requested_by=models.F("confirmed_by")),
                name="vendors_change_second_person",
            ),
        ]

    def __str__(self) -> str:
        return f"Bank account change {self.id} ({self.status})"
