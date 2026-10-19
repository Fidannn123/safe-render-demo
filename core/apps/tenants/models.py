"""Synthetic business metadata. Financial state lives in TenantLimits under its row lock."""

from __future__ import annotations

import uuid

from django.db import models


class Tenant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=140)
    sector = models.CharField(max_length=64, default="retail")
    bank_account_id = models.UUIDField(default=uuid.uuid4)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "tenants_tenant"

    def __str__(self) -> str:
        return self.name
