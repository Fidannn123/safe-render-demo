"""Idempotency-Key store for the API layer (architecture.md 2.5, DECISIONS.md D-016).

run_idempotent() stores the key, the request SHA-256 and the response in the SAME transaction as the
write performed by `handler`:
  * first request: the key row is inserted, handler runs, its response is stored, all commit together;
  * replay with the same request hash: the stored response is returned, handler is not called;
  * same key, different request: 409 IDEMPOTENCY_MISMATCH;
  * a concurrent duplicate blocks on the uncommitted key row and then sees the stored response.
If handler raises, everything (including the key row) rolls back, so the client may retry.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from django.db import connection, transaction
from django.utils import timezone

from apps.ledger.tenancy import bind_tenant

from .errors import ProblemError
from .models import IdempotencyKey

KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")  # openapi-core.yaml components.parameters.IdempotencyKey


@dataclass(frozen=True, slots=True)
class IdempotentResult:
    status: int
    body: Any
    replayed: bool


def request_sha256(method: str, path: str, body: bytes) -> str:
    """SHA-256 over METHOD \\n PATH \\n hex(SHA-256(body)) (binds the key to the exact request)."""
    material = f"{method.upper()}\n{path}\n{hashlib.sha256(body).hexdigest()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def run_idempotent(
    tenant_id: uuid.UUID,
    key: str | None,
    request_hash: str,
    handler: Callable[[], tuple[int, Any]],
) -> IdempotentResult:
    if not key:
        raise ProblemError("IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header required")
    if not KEY_RE.match(key):
        raise ProblemError("VALIDATION_FAILED", "Idempotency-Key must match ^[A-Za-z0-9._:-]{16,128}$")
    with transaction.atomic():
        bind_tenant(tenant_id)
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO payments_idempotency_key (tenant_id, key, request_sha256, status, created_at) "
                "VALUES (%s, %s, %s, %s, now()) ON CONFLICT (tenant_id, key) DO NOTHING RETURNING id",
                [str(tenant_id), key, request_hash, IdempotencyKey.STATUS_IN_PROGRESS],
            )
            inserted = cur.fetchone() is not None
        row = IdempotencyKey.objects.select_for_update().get(tenant_id=tenant_id, key=key)
        if row.request_sha256 != request_hash:
            raise ProblemError("IDEMPOTENCY_MISMATCH", "Idempotency-Key reused with a different request")
        if not inserted:
            if row.status != IdempotencyKey.STATUS_COMPLETED or row.response_status is None:
                raise ProblemError("IDEMPOTENCY_IN_PROGRESS", "request with this key still in progress")
            return IdempotentResult(status=row.response_status, body=row.response_body, replayed=True)
        status, body = handler()
        row.status = IdempotencyKey.STATUS_COMPLETED
        row.response_status = status
        row.response_body = body
        row.completed_at = timezone.now()
        row.save(update_fields=["status", "response_status", "response_body", "completed_at"])
        return IdempotentResult(status=status, body=body, replayed=False)
