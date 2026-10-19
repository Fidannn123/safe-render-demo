"""Audit hash chain: genesis, gapless seq, RFC 8785 hashing, tamper detection, DB link trigger."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import jsonschema
import pytest
import rfc8785
from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection

from apps.audit.chain import (
    GENESIS_HASH,
    AuditChainError,
    append_event,
    compute_hash,
    event_body,
    event_document,
    verify_chain,
)
from apps.audit.models import AuditEvent
from apps.ledger.tenancy import tenant_transaction

pytestmark = pytest.mark.django_db(transaction=True)
ACTOR = {"type": "system", "id": "system"}


def _append(tenant: uuid.UUID, n: int = 1) -> list[AuditEvent]:
    out = []
    for i in range(n):
        with tenant_transaction(tenant):
            out.append(
                append_event(
                    tenant_id=tenant,
                    actor=ACTOR,
                    action="PAYMENT_TRANSITION",
                    subject_type="payment",
                    subject_id=str(uuid.uuid4()),
                    data={"from": None, "to": "RECEIVED", "trigger": "invoice_received", "i": i},
                )
            )
    return out


def _schema_validator() -> jsonschema.Draft202012Validator:
    from referencing import Registry, Resource  # noqa: PLC0415

    schemas = Path(settings.SAFEPAY_SPEC_DIR) / "schemas"
    registry: Registry = Registry()
    for path in schemas.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(path.name, Resource.from_contents(doc))
    schema = json.loads((schemas / "AuditEvent.json").read_text(encoding="utf-8"))
    return jsonschema.Draft202012Validator(schema, registry=registry)


def test_genesis_and_links() -> None:
    tenant = uuid.uuid4()
    evs = _append(tenant, 3)
    assert [e.seq for e in evs] == [1, 2, 3]
    assert evs[0].prev_hash == GENESIS_HASH
    assert evs[1].prev_hash == evs[0].hash
    assert evs[2].prev_hash == evs[1].hash
    with tenant_transaction(tenant):
        assert verify_chain(tenant) == 3


def test_hash_definition_matches_spec() -> None:
    tenant = uuid.uuid4()
    [ev] = _append(tenant)
    body = event_body(ev)
    assert "hash" not in body
    expected = hashlib.sha256(bytes.fromhex(GENESIS_HASH) + rfc8785.dumps(body)).hexdigest()
    assert ev.hash == expected
    _schema_validator().validate(event_document(ev))


def test_stored_row_recomputes() -> None:
    tenant = uuid.uuid4()
    [ev] = _append(tenant)
    with tenant_transaction(tenant):
        stored = AuditEvent.objects.get(event_id=ev.event_id)
    assert compute_hash(stored.prev_hash, event_body(stored)) == stored.hash


def test_tenants_have_independent_chains() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    _append(a, 2)
    [first_b] = _append(b)
    assert first_b.seq == 1
    assert first_b.prev_hash == GENESIS_HASH


def test_tampered_event_detected() -> None:
    tenant = uuid.uuid4()
    [ev] = _append(tenant)
    ev.data = {**ev.data, "i": 99}
    assert compute_hash(ev.prev_hash, event_body(ev)) != ev.hash


def test_verify_detects_forged_hash_inserted_by_owner() -> None:
    """Even a writer that bypasses append_event cannot insert a well-linked event with a wrong hash
    unnoticed: the DB link trigger accepts it (it links), but verify_chain recomputes and fails."""
    tenant = uuid.uuid4()
    [ev] = _append(tenant)
    with tenant_transaction(tenant):
        AuditEvent.objects.create(
            tenant_id=tenant,
            seq=2,
            occurred_at=datetime.now(UTC),
            actor=ACTOR,
            action="PAYMENT_TRANSITION",
            subject_type="payment",
            subject_id="x",
            data={},
            prev_hash=ev.hash,
            hash="f" * 64,
        )
    with pytest.raises(AuditChainError, match="seq 2"), tenant_transaction(tenant):
        verify_chain(tenant)


@pytest.mark.parametrize(("seq", "prev"), [(1, "1" * 64), (2, GENESIS_HASH), (3, None)])
def test_db_rejects_broken_links(seq: int, prev: str | None) -> None:
    tenant = uuid.uuid4()
    [ev] = _append(tenant)
    with pytest.raises(IntegrityError), tenant_transaction(tenant):
        AuditEvent.objects.create(
            tenant_id=tenant,
            seq=seq,
            occurred_at=datetime.now(UTC),
            actor=ACTOR,
            action="X_TEST",
            subject_type="payment",
            subject_id="x",
            data={},
            prev_hash=prev or ev.hash,
            hash="e" * 64,
        )


def test_events_cannot_be_updated_even_by_owner() -> None:
    tenant = uuid.uuid4()
    _append(tenant)
    with (
        pytest.raises(DatabaseError, match="append-only"),
        tenant_transaction(tenant),
        connection.cursor() as cur,
    ):
        cur.execute("UPDATE audit_event SET data = '{}'::jsonb")


def test_append_requires_transaction() -> None:
    with pytest.raises(RuntimeError):
        append_event(
            tenant_id=uuid.uuid4(),
            actor=ACTOR,
            action="X_TEST",
            subject_type="payment",
            subject_id="x",
            data={},
        )
