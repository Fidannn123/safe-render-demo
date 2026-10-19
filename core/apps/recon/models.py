"""Durable reconciliation incidents; a discrepancy freezes outflow through TenantLimits."""

from __future__ import annotations

import uuid

from django.db import models


class Incident(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    fingerprint = models.CharField(max_length=64)
    code = models.CharField(max_length=40)
    payment_id = models.UUIDField(null=True, blank=True)
    detail = models.JSONField(default=dict)
    freezes_tenant = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "recon_incident"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(
                fields=["tenant_id", "fingerprint"], name="recon_incident_tenant_fingerprint_uniq"
            )
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.id}"
