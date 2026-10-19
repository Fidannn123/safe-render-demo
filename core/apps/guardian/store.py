"""Persistence of issued Decisions (append-only guardian_decision). The only DB code in guardian.

Callers run inside a transaction that has set app.tenant_id (RLS); record_decision sets it LOCAL to
the Decision's tenant so the insert passes the WITH CHECK policy even if the caller forgot.
"""

from __future__ import annotations

import datetime as dt

from django.db import connection, transaction

from .decide import Decision, IssuedDecision, UnsignedDecision
from .models import GuardianDecision


def _parse_ts(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def set_tenant(tenant_id: str) -> None:
    """SET LOCAL app.tenant_id for the current transaction (RLS)."""
    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, true)", [tenant_id])


def record_decision(issued: IssuedDecision) -> GuardianDecision:
    d = issued.decision
    with transaction.atomic():
        set_tenant(d.tenant_id)
        return GuardianDecision.objects.create(
            decision_id=d.decision_id,
            tenant_id=d.tenant_id,
            payment_id=d.payment_id,
            verdict=d.verdict,
            tier=d.tier,
            score=d.score,
            reasons=[r.model_dump(mode="json") for r in d.reasons],
            policy_version=d.policy_version,
            network_epoch=d.network_epoch,
            inputs_sha256=d.inputs_sha256,
            inputs_canonical=issued.canonical_inputs.decode("utf-8"),
            nonce=d.nonce,
            issued_at=_parse_ts(d.issued_at),
            expires_at=_parse_ts(d.expires_at),
            key_id=d.key_id,
            sig=d.sig,
            signed_payload=d.unsigned().canonical().decode("utf-8"),
        )


def load_decision(row: GuardianDecision) -> Decision:
    """Rebuild the signed Decision from its stored signed payload and signature."""
    unsigned = UnsignedDecision.model_validate_json(row.signed_payload)
    return Decision(**unsigned.model_dump(), sig=row.sig)
