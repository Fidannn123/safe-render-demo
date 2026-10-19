"""Write and verify the per-tenant audit hash chain (spec/schemas/AuditEvent.json).

append_event() must run inside a transaction with app.tenant_id bound to the event's tenant. A
transaction-scoped advisory lock per tenant serialises writers, so seq has no gaps (a rolled-back
transaction releases its seq) and every event links to the previous hash.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

import rfc8785
from django.db import connection, transaction
from django.utils import timezone

from .models import AuditEvent

GENESIS_HASH = "0" * 64
ADVISORY_NAMESPACE = "safepay:audit:"


class AuditChainError(Exception):
    """The stored chain does not recompute (tampering, gap or broken link)."""


def format_timestamp(dt: datetime) -> str:
    """RFC 3339 UTC with microseconds and suffix Z (lossless for PostgreSQL timestamptz)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def event_body(ev: AuditEvent) -> dict[str, Any]:
    """The AuditEvent object without `hash`: exactly the bytes that are hashed."""
    return {
        "seq": ev.seq,
        "event_id": str(ev.event_id),
        "tenant_id": str(ev.tenant_id),
        "occurred_at": format_timestamp(ev.occurred_at),
        "actor": ev.actor,
        "action": ev.action,
        "subject": {"type": ev.subject_type, "id": ev.subject_id},
        "data": ev.data,
        "prev_hash": ev.prev_hash,
    }


def event_document(ev: AuditEvent) -> dict[str, Any]:
    """Full AuditEvent JSON (schema shape)."""
    return {**event_body(ev), "hash": ev.hash}


def compute_hash(prev_hash: str, body: dict[str, Any]) -> str:
    return hashlib.sha256(bytes.fromhex(prev_hash) + rfc8785.dumps(body)).hexdigest()


def _lock_tenant_chain(tenant_id: uuid.UUID) -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", [f"{ADVISORY_NAMESPACE}{tenant_id}"]
        )


def append_event(
    *,
    tenant_id: uuid.UUID,
    actor: dict[str, str],
    action: str,
    subject_type: str,
    subject_id: str,
    data: dict[str, Any],
    occurred_at: datetime | None = None,
) -> AuditEvent:
    if not connection.in_atomic_block:
        raise RuntimeError("append_event must run inside transaction.atomic()")
    _lock_tenant_chain(tenant_id)
    last = AuditEvent.objects.filter(tenant_id=tenant_id).order_by("-seq").only("seq", "hash").first()
    seq, prev_hash = (last.seq + 1, last.hash) if last else (1, GENESIS_HASH)
    when = (occurred_at or timezone.now()).astimezone(UTC)
    ev = AuditEvent(
        event_id=uuid.uuid4(),
        tenant_id=tenant_id,
        seq=seq,
        occurred_at=when,
        actor=dict(actor),
        action=action,
        subject_type=subject_type,
        subject_id=subject_id,
        data=data,
        prev_hash=prev_hash,
        hash="",
    )
    ev.hash = compute_hash(prev_hash, event_body(ev))
    ev.save(force_insert=True)
    return ev


def verify_chain(tenant_id: uuid.UUID) -> int:
    """Recompute the tenant's chain from genesis. Returns the number of events; raises AuditChainError."""
    prev_hash, expected_seq = GENESIS_HASH, 1
    with transaction.atomic():
        for ev in AuditEvent.objects.filter(tenant_id=tenant_id).order_by("seq").iterator():
            if ev.seq != expected_seq:
                raise AuditChainError(f"gap: expected seq {expected_seq}, found {ev.seq}")
            if ev.prev_hash != prev_hash:
                raise AuditChainError(f"seq {ev.seq}: prev_hash does not link")
            if compute_hash(prev_hash, event_body(ev)) != ev.hash:
                raise AuditChainError(f"seq {ev.seq}: hash does not recompute")
            prev_hash, expected_seq = ev.hash, expected_seq + 1
    return expected_seq - 1
