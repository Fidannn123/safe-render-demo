"""Automatic T4 freeze of a misbehaving agent token (spec/policy.yaml `velocity`, DECISIONS.md D-046).

An honest agent never calls a human-only endpoint and rarely proposes a payment the Guardian DENIES.
Each such refusal is written to the audit chain as AGENT_REQUEST_REFUSED. When one token collects
`refusals_to_freeze` refusals within `window_seconds`, the tenant is frozen exactly as by the freeze
button: outflow stops and every agent token is revoked. Unfreezing still needs the owner and step-up.
"""

from __future__ import annotations

import functools
import uuid
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml
from django.conf import settings
from django.utils import timezone

from apps.audit.chain import append_event
from apps.audit.models import AuditEvent
from apps.ledger.tenancy import tenant_transaction
from apps.payments.types import Actor

REFUSED = "AGENT_REQUEST_REFUSED"
VELOCITY_ACTOR = Actor("system", "velocity-monitor")


@dataclass(frozen=True)
class VelocityPolicy:
    refusals_to_freeze: int
    window: timedelta


@functools.cache
def velocity_policy() -> VelocityPolicy:
    path = Path(settings.SAFEPAY_SPEC_DIR) / "policy.yaml"
    raw: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))["velocity"]
    refusals, seconds = int(raw["refusals_to_freeze"]), int(raw["window_seconds"])
    if refusals < 1 or seconds < 1:
        raise ValueError("velocity policy values must be positive")
    return VelocityPolicy(refusals_to_freeze=refusals, window=timedelta(seconds=seconds))


def record_refusal(tenant_id: uuid.UUID, token_id: str, reason: str) -> bool:
    """Audit one refused agent request in its own transaction; freeze the tenant at the threshold.

    Called after the refused request's own transaction rolled back. Returns True when this refusal
    froze the tenant.
    """
    from .service import set_frozen  # noqa: PLC0415 (keeps the middleware import light)

    policy = velocity_policy()
    with tenant_transaction(tenant_id):
        append_event(
            tenant_id=tenant_id,
            actor={"type": "agent", "id": token_id},
            action=REFUSED,
            subject_type="agent_token",
            subject_id=token_id,
            data={"reason": reason},
        )
        since = timezone.now() - policy.window
        recent = AuditEvent.objects.filter(
            tenant_id=tenant_id,
            action=REFUSED,
            subject_type="agent_token",
            subject_id=token_id,
            occurred_at__gt=since,
        ).count()
        if recent < policy.refusals_to_freeze:
            return False
        status = set_frozen(
            tenant_id,
            VELOCITY_ACTOR,
            True,
            f"automatic: agent token made {recent} refused requests within "
            f"{int(policy.window.total_seconds())} s",
        )
        return bool(status["frozen"])
