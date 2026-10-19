"""Signed checkpoint coverage, tenant binding and tamper detection on real PostgreSQL."""

from __future__ import annotations

import copy
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone

from apps.audit.chain import AuditChainError, append_event, event_document
from apps.audit.checkpoints import checkpoint, verify_documents
from apps.audit.models import AuditEvent
from apps.keystore.file_store import FileKeyStore
from apps.ledger.tenancy import tenant_transaction

pytestmark = pytest.mark.django_db(transaction=True)


def test_checkpoint_links_and_tampering(tmp_path: Path) -> None:
    tenant, store = uuid.uuid4(), FileKeyStore(tmp_path)
    with tenant_transaction(tenant):
        append_event(
            tenant_id=tenant,
            actor={"type": "system", "id": "test"},
            action="TEST",
            subject_type="tenant",
            subject_id=str(tenant),
            data={"n": 1},
        )
        first = checkpoint(tenant, force=True, keystore=store)
        assert first is not None
        assert checkpoint(tenant, force=True, keystore=store) is None
        append_event(
            tenant_id=tenant,
            actor={"type": "system", "id": "test"},
            action="TEST",
            subject_type="tenant",
            subject_id=str(tenant),
            data={"n": 2},
        )
        second = checkpoint(tenant, force=True, keystore=store)
        assert second is not None
        events = [event_document(event) for event in AuditEvent.objects.order_by("seq")]
        documents = [first.document, second.document]
    assert verify_documents(events, documents, store) == 2
    bad_events = copy.deepcopy(events)
    bad_events[0]["data"]["n"] = 500
    with pytest.raises(AuditChainError, match="hash mismatch"):
        verify_documents(bad_events, documents, store)
    bad_documents = copy.deepcopy(documents)
    bad_documents[1]["sig"] = "A" * 88
    with pytest.raises(AuditChainError, match="signature invalid"):
        verify_documents(events, bad_documents, store)
    with pytest.raises(AuditChainError, match="unsigned"):
        verify_documents(events, documents[:1], store)


def test_checkpoint_interval_and_empty_chain(tmp_path: Path) -> None:
    tenant, store = uuid.uuid4(), FileKeyStore(tmp_path)
    assert checkpoint(tenant, force=True, keystore=store) is None
    now = timezone.now()
    with tenant_transaction(tenant):
        append_event(
            tenant_id=tenant,
            actor={"type": "system", "id": "test"},
            action="TEST",
            subject_type="tenant",
            subject_id=str(tenant),
            data={},
            occurred_at=now,
        )
    assert checkpoint(tenant, now=now, keystore=store) is None
    assert checkpoint(tenant, now=now + timedelta(minutes=5), keystore=store) is not None
    empty: list[dict[str, Any]] = []
    assert verify_documents(empty, empty, store) == 0
