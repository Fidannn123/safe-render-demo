"""Ed25519 audit checkpoints and independent export verification (synthetic prototype)."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Any

import rfc8785
from django.utils import timezone

from apps.keystore.file_store import default_keystore
from apps.keystore.port import KeyId, KeyStore, b64encode, verify_signature
from apps.ledger.tenancy import tenant_transaction

from .chain import GENESIS_HASH, AuditChainError, _lock_tenant_chain, compute_hash, format_timestamp
from .models import AuditCheckpoint, AuditEvent


def checkpoint(
    tenant_id: uuid.UUID,
    *,
    force: bool = False,
    now: datetime | None = None,
    keystore: KeyStore | None = None,
) -> AuditCheckpoint | None:
    """Sign after 100 new events or five minutes; force signs the current uncovered head."""
    when = now or timezone.now()
    with tenant_transaction(tenant_id):
        _lock_tenant_chain(tenant_id)
        last = AuditCheckpoint.objects.filter(tenant_id=tenant_id).order_by("-seq_to").first()
        head = AuditEvent.objects.filter(tenant_id=tenant_id).order_by("-seq").first()
        start = last.seq_to + 1 if last else 1
        if head is None or head.seq < start:
            return None
        first = AuditEvent.objects.get(tenant_id=tenant_id, seq=start)
        if not force and head.seq - start + 1 < 100 and when - first.occurred_at < timedelta(minutes=5):
            return None
        store = keystore or default_keystore()
        key_id = store.active_key_id(tenant_id, "audit")
        document: dict[str, Any] = {
            "checkpoint_id": str(uuid.uuid4()),
            "tenant_id": str(tenant_id),
            "seq_from": start,
            "seq_to": head.seq,
            "event_count": head.seq - start + 1,
            "head_hash": head.hash,
            "prev_checkpoint_hash": hashlib.sha256(rfc8785.dumps(last.document)).hexdigest()
            if last
            else None,
            "created_at": format_timestamp(when),
            "key_id": key_id,
        }
        document["sig"] = b64encode(store.sign(key_id, rfc8785.dumps(document)))
        return AuditCheckpoint.objects.create(
            checkpoint_id=document["checkpoint_id"],
            tenant_id=tenant_id,
            seq_from=start,
            seq_to=head.seq,
            document=document,
            created_at=when,
        )


def verify_documents(
    events: list[dict[str, Any]], checkpoints: list[dict[str, Any]], keystore: KeyStore
) -> int:
    """Verify every event and checkpoint; require complete signed coverage of a nonempty chain."""
    previous = GENESIS_HASH
    heads: dict[int, str] = {}
    tenant = events[0]["tenant_id"] if events else None
    for seq, event in enumerate(events, 1):
        body = {key: value for key, value in event.items() if key != "hash"}
        if event["seq"] != seq or event["tenant_id"] != tenant or event["prev_hash"] != previous:
            raise AuditChainError(f"event {seq}: sequence, tenant or previous hash mismatch")
        if event["hash"] != compute_hash(previous, body):
            raise AuditChainError(f"event {seq}: hash mismatch")
        previous = event["hash"]
        heads[seq] = previous
    last_seq, previous_checkpoint = 0, None
    for document in checkpoints:
        end, start = document["seq_to"], document["seq_from"]
        key = KeyId.parse(document["key_id"])
        if key.purpose != "audit" or str(key.tenant_id) != tenant:
            raise AuditChainError("checkpoint key is not bound to the tenant audit purpose")
        if document["tenant_id"] != tenant or start != last_seq + 1 or end < start:
            raise AuditChainError("checkpoint ranges are not contiguous")
        if document["event_count"] != end - start + 1 or heads.get(end) != document["head_hash"]:
            raise AuditChainError("checkpoint head or count mismatch")
        if document["prev_checkpoint_hash"] != previous_checkpoint:
            raise AuditChainError("checkpoint link mismatch")
        unsigned = {key: value for key, value in document.items() if key != "sig"}
        if not verify_signature(
            keystore.public_key(document["key_id"]), rfc8785.dumps(unsigned), document["sig"]
        ):
            raise AuditChainError("checkpoint signature invalid")
        previous_checkpoint = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
        last_seq = end
    if last_seq != len(events):
        raise AuditChainError("audit chain has unsigned events")
    return len(events)
