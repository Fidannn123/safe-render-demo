"""Member request-signature authentication for every /v1 endpoint (DECISIONS.md D-012).

Order of checks: headers present and well-formed -> key known and not revoked -> timestamp within skew ->
signature verifies -> member not suspended -> nonce unused (recorded atomically). Runs before ninja
parses the body, so unauthenticated callers never reach validation.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction
from django.http import HttpRequest
from ninja.security import APIKeyHeader

from apps.members.models import Member, MemberKey, RequestNonce
from safepay_intel.crypto import KeyFormatError, public_key_from_raw, request_signing_string, verify_b64
from safepay_intel.problems import ProblemError

SKEW_SECONDS = 300
_TIMESTAMP = re.compile(r"^[0-9]{10}$")
_NONCE = re.compile(r"^[0-9a-f]{32,64}$")
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")


@dataclass(frozen=True)
class MemberContext:
    member: Member
    key_id: str


def _header(request: HttpRequest, name: str) -> str:
    return str(request.headers.get(name, ""))


def authenticate_request(request: HttpRequest, now: float | None = None) -> MemberContext:
    key_id = _header(request, "X-SafePay-Key-Id")
    timestamp = _header(request, "X-SafePay-Timestamp")
    nonce = _header(request, "X-SafePay-Nonce")
    signature = _header(request, "X-SafePay-Signature")
    if not (
        KEY_ID_PATTERN.match(key_id) and _TIMESTAMP.match(timestamp) and _NONCE.match(nonce) and signature
    ):
        raise ProblemError("SIGNATURE_INVALID", "missing or malformed signing headers")
    key = MemberKey.objects.select_related("member").filter(key_id=key_id, revoked_at__isnull=True).first()
    if key is None:
        raise ProblemError("SIGNATURE_INVALID", "unknown or revoked key")
    current = time.time() if now is None else now
    if abs(current - int(timestamp)) > SKEW_SECONDS:
        raise ProblemError("REQUEST_EXPIRED", f"timestamp outside {SKEW_SECONDS} s skew")
    path_qs = request.get_full_path()
    message = request_signing_string(request.method or "", path_qs, timestamp, nonce, request.body)
    try:
        public_key = public_key_from_raw(bytes(key.public_key))
    except KeyFormatError as exc:
        raise ProblemError("SIGNATURE_INVALID", "stored key malformed") from exc
    if not verify_b64(public_key, message, signature):
        raise ProblemError("SIGNATURE_INVALID", "signature does not verify")
    if key.member.status != Member.Status.ACTIVE:
        raise ProblemError("MEMBER_SUSPENDED")
    try:
        with transaction.atomic():
            RequestNonce.objects.create(key_id=key_id, nonce=nonce)
    except IntegrityError as exc:
        raise ProblemError("NONCE_REPLAYED") from exc
    return MemberContext(member=key.member, key_id=key_id)


class MemberSignature(APIKeyHeader):
    """ninja security scheme `memberSignature` (spec/openapi-intel.yaml)."""

    param_name = "X-SafePay-Signature"
    openapi_name = "memberSignature"

    def __call__(self, request: HttpRequest) -> Any:
        return authenticate_request(request)

    def authenticate(self, request: HttpRequest, key: str | None) -> Any:
        return authenticate_request(request)


def request_member(request: HttpRequest) -> MemberContext:
    ctx = getattr(request, "auth", None)
    if not isinstance(ctx, MemberContext):
        raise ProblemError("SIGNATURE_INVALID")
    return ctx
