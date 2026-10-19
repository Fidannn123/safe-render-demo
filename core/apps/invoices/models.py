"""Quarantined invoice source and validated extraction, always tenant scoped."""

from __future__ import annotations

import uuid

from django.db import models


class Invoice(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    source = models.JSONField()
    extracted = models.JSONField(null=True)
    received_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "invoices_invoice"

    def __str__(self) -> str:
        return f"Invoice {self.id}"
