"""Request-signature authentication for /v1 and /admin (DECISIONS.md D-012).

Order: required headers well-formed -> known key id -> Ed25519 signature over the canonical request
-> timestamp within skew -> nonce single-use (consumed only after the signature verified, so forged
requests cannot burn nonces). Every rejection is RFC 9457 problem+json (401).
HUMAN REVIEW: authentication boundary of the bank sandbox.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from django.conf import settings
from django.db import IntegrityError, transaction
from django.http import HttpRequest, HttpResponse

from safepay_bank import clock
from safepay_bank.keys import caller_public_key
from safepay_bank.models import RequestNonce
from safepay_bank.problems import problem_response
from safepay_bank.reqsig import (
    HEADER_KEY_ID,
    HEADER_NONCE,
    HEADER_SIGNATURE,
    HEADER_TIMESTAMP,
    KEY_ID_RE,
    NONCE_RE,
    TIMESTAMP_RE,
    verify_request,
)

log = logging.getLogger("safepay_bank.auth")

PROTECTED_PREFIXES = ("/v1/", "/admin/")
UNSIGNED_PATHS = frozenset({"/v1/openapi.json"})


def _requires_signature(path: str) -> bool:
    return path.startswith(PROTECTED_PREFIXES) and path not in UNSIGNED_PATHS


class RequestSignatureMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if _requires_signature(request.path):
            rejection = self.authenticate(request)
            if rejection is not None:
                return rejection
        return self.get_response(request)

    def authenticate(self, request: HttpRequest) -> HttpResponse | None:
        key_id = request.headers.get(HEADER_KEY_ID, "")
        timestamp = request.headers.get(HEADER_TIMESTAMP, "")
        nonce = request.headers.get(HEADER_NONCE, "")
        signature = request.headers.get(HEADER_SIGNATURE, "")
        instance = request.path
        if not (
            KEY_ID_RE.match(key_id) and TIMESTAMP_RE.match(timestamp) and NONCE_RE.match(nonce) and signature
        ):
            return problem_response("SIGNATURE_INVALID", "missing or malformed signing headers", instance)
        public_key = caller_public_key(key_id)
        if public_key is None:
            log.warning("unknown request key id %s", key_id)
            return problem_response("SIGNATURE_INVALID", "unknown key id", instance)
        query = request.META.get("QUERY_STRING", "")
        if not verify_request(
            public_key,
            method=request.method or "",
            path=request.path,
            query=query,
            timestamp=timestamp,
            nonce=nonce,
            body=request.body,
            signature_b64=signature,
        ):
            log.warning("bad request signature key_id=%s path=%s", key_id, request.path)
            return problem_response("SIGNATURE_INVALID", "signature does not verify", instance)
        now = int(clock.now().timestamp())
        skew = int(settings.SAFEPAY_REQUEST_SKEW_SECONDS)
        ts = int(timestamp)
        if abs(now - ts) > skew:
            return problem_response("REQUEST_EXPIRED", f"timestamp outside +-{skew} s", instance)
        try:
            with transaction.atomic():
                RequestNonce.objects.filter(request_ts__lt=now - 2 * skew).delete()
                RequestNonce.objects.create(nonce=nonce, key_id=key_id, request_ts=ts)
        except IntegrityError:
            log.warning("replayed nonce key_id=%s path=%s", key_id, request.path)
            return problem_response("NONCE_REPLAYED", "nonce already used", instance)
        return None
