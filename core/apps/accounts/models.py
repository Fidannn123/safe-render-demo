"""Custom user model, created at G0 so it exists before the first migration (Django requirement).

M8 extends it (roles, TOTP devices, lockout). UUID primary key: no enumerable user ids.
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField(null=True)
    role = models.CharField(max_length=16, default="accountant")

    class Meta:
        db_table = "accounts_user"


class AgentToken(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    name = models.CharField(max_length=64)
    digest = models.CharField(max_length=64, unique=True)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
    created_by = models.UUIDField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_agent_token"

    def __str__(self) -> str:
        return f"{self.name} ({self.id})"  # never the digest
