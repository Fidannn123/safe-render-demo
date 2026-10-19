"""Append-only, per-tenant hash-chained audit log (spec/schemas/AuditEvent.json).

hash = SHA-256(bytes.fromhex(prev_hash) || RFC 8785 canonical JSON(event without hash)); genesis
prev_hash is 64 zeros; seq starts at 1 without gaps. PostgreSQL enforces UNIQUE(tenant_id, seq) and,
with a BEFORE INSERT trigger, that each new event links to the previous event's hash (migration 0002).
audit_event is append-only (db_policy.py).
"""

from __future__ import annotations

import uuid

from django.db import models


class AuditEvent(models.Model):
    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    seq = models.BigIntegerField()
    occurred_at = models.DateTimeField()
    actor = models.JSONField()
    action = models.CharField(max_length=64)
    subject_type = models.CharField(max_length=32)
    subject_id = models.CharField(max_length=128)
    data = models.JSONField()
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)

    class Meta:
        db_table = "audit_event"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["tenant_id", "seq"], name="audit_event_tenant_seq_uniq"),
            models.CheckConstraint(condition=models.Q(seq__gte=1), name="audit_event_seq_chk"),
            models.CheckConstraint(
                condition=models.Q(prev_hash__regex=r"^[0-9a-f]{64}$")
                & models.Q(hash__regex=r"^[0-9a-f]{64}$"),
                name="audit_event_hash_hex_chk",
            ),
        ]
        indexes = [  # noqa: RUF012
            models.Index(fields=["tenant_id", "subject_type", "subject_id"], name="audit_event_subject_idx")
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id}#{self.seq} {self.action}"


class AuditCheckpoint(models.Model):
    """Immutable signed checkpoint, linked to the previous checkpoint document."""

    checkpoint_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    seq_from = models.BigIntegerField()
    seq_to = models.BigIntegerField()
    document = models.JSONField()
    created_at = models.DateTimeField()

    class Meta:
        db_table = "audit_checkpoint"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["tenant_id", "seq_to"], name="audit_checkpoint_tenant_seq_uniq"),
            models.CheckConstraint(
                condition=models.Q(seq_from__gte=1, seq_to__gte=models.F("seq_from")),
                name="audit_checkpoint_range_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.tenant_id}#{self.seq_from}..{self.seq_to}"
