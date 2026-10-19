"""Human roles, replay-protected django-otp TOTP and HMAC-stored, expiring agent credentials."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.payments.errors import ProblemError
from apps.payments.guards import step_up_max_age
from apps.payments.types import Actor

from .models import AgentToken, User
from .secrets import random_key


@dataclass(frozen=True)
class Principal:
    tenant_id: uuid.UUID
    actor: Actor
    role: str | None = None
    scopes: tuple[str, ...] = ()
    user: User | None = None


def token_digest(value: str) -> str:
    return hmac.new(random_key("agent_token_pepper"), value.encode(), hashlib.sha256).hexdigest()


def issue_token(
    tenant_id: uuid.UUID, user_id: uuid.UUID, name: str, scopes: list[str], ttl_seconds: int = 3600
) -> dict[str, Any]:
    if not scopes or not set(scopes) <= {"T0", "T1", "T2"} or not 60 <= ttl_seconds <= 3600:
        raise ProblemError("VALIDATION_FAILED", "Only T0-T2 scopes with a 60-3600 second TTL are permitted")
    value = "spa_" + secrets.token_urlsafe(32)
    token = AgentToken.objects.create(
        tenant_id=tenant_id,
        created_by=user_id,
        name=name,
        scopes=sorted(set(scopes)),
        digest=token_digest(value),
        expires_at=timezone.now() + timedelta(seconds=ttl_seconds),
    )
    return {
        "token_id": str(token.id),
        "token": value,
        "scopes": token.scopes,
        "expires_at": token.expires_at.isoformat(),
    }


def authenticate_token(value: str) -> Principal:
    token = AgentToken.objects.filter(
        digest=token_digest(value), revoked_at__isnull=True, expires_at__gt=timezone.now()
    ).first()
    if token is None:
        raise ProblemError("UNAUTHENTICATED", "Invalid, expired or revoked agent token")
    return Principal(token.tenant_id, Actor("agent", str(token.id)), scopes=tuple(token.scopes))


def require(
    request: HttpRequest,
    *,
    roles: tuple[str, ...] = ("owner", "approver", "accountant"),
    scopes: tuple[str, ...] = (),
    step_up: bool = False,
    agent_only: bool = False,
) -> Principal:
    principal: Principal | None = getattr(request, "principal", None)
    if principal is None:
        raise ProblemError("UNAUTHENTICATED", "Authentication required")
    if principal.actor.type == "agent":
        if not scopes or not set(principal.scopes).intersection(scopes):
            raise ProblemError("FORBIDDEN_SCOPE", "Agent token cannot access this operation")
    else:
        if agent_only or principal.role not in roles:
            raise ProblemError("FORBIDDEN_SCOPE", "Role is not permitted")
        if step_up:
            fresh_step_up(request)
    return principal


def fresh_step_up(request: HttpRequest) -> datetime:
    raw = request.session.get("step_up_at")
    at = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    now = timezone.now()
    if at is None or at > now or now - at > step_up_max_age():
        raise ProblemError("STEP_UP_REQUIRED", "A fresh one-time password is required")
    return at


def verify_otp(user: User, otp: str) -> bool:
    if not otp.isascii() or not otp.isdigit() or len(otp) != 6:
        return False
    # Device row lock prevents two concurrent requests from replaying the same time-step token.
    with transaction.atomic():
        for device in TOTPDevice.objects.select_for_update().filter(user=user, confirmed=True):
            if device.verify_token(otp):
                return True
    return False


def session_view(request: HttpRequest, user: User) -> dict[str, Any]:
    raw = request.session.get("step_up_at", timezone.now().isoformat())
    return {
        "user_id": str(user.id),
        "tenant_id": str(user.tenant_id),
        "roles": [user.role],
        "step_up_valid_until": (datetime.fromisoformat(raw) + step_up_max_age()).isoformat(),
    }
