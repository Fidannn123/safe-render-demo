"""Network members (admission-controlled: created only by `manage.py create_member`; KYB in production)."""

from __future__ import annotations

import uuid

from django.db import models


class Member(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE"
        SUSPENDED = "SUSPENDED"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=140)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "members_member"

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"


class MemberKey(models.Model):
    """Ed25519 request/report signing key of a member (raw 32-byte public key)."""

    key_id = models.CharField(max_length=128, primary_key=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="keys")
    public_key = models.BinaryField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "members_member_key"

    def __str__(self) -> str:
        return self.key_id


class MemberAccount(models.Model):
    """A bank account (simulated bank account_id + IBAN) attested to belong to the member at admission."""

    account_id = models.UUIDField(primary_key=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="accounts")
    iban = models.CharField(max_length=34)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "members_member_account"

    def __str__(self) -> str:
        return f"{self.member_id}:{self.account_id}"


class RequestNonce(models.Model):
    """Single-use request nonces (D-012). Kept, never deleted: uniqueness is stricter than the skew window."""

    key_id = models.CharField(max_length=128)
    nonce = models.CharField(max_length=64)
    seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "members_request_nonce"
        constraints = (models.UniqueConstraint(fields=["key_id", "nonce"], name="members_nonce_once"),)

    def __str__(self) -> str:
        return f"{self.key_id}:{self.nonce}"
