from __future__ import annotations

import uuid

from django.db import models


class ProbeTenantRow(models.Model):
    """Ordinary tenant table: RLS applies (tenant_id column)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField()
    note = models.CharField(max_length=64)

    class Meta:
        db_table = "dbprobe_tenant_row"

    def __str__(self) -> str:
        return f"probe {self.id}"


class ProbeAppendOnlyRow(models.Model):
    """Append-only tenant table (listed in SAFEPAY_APPEND_ONLY_TABLES by settings.test)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    tenant_id = models.UUIDField()
    amount_minor = models.BigIntegerField()

    class Meta:
        db_table = "dbprobe_append_only"

    def __str__(self) -> str:
        return f"probe {self.id}"
