"""Bind the PostgreSQL row-level-security tenant (app.tenant_id) to the current transaction."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from django.db import connection, transaction


class TenantMismatchError(RuntimeError):
    """The transaction is already bound to a different tenant."""


def current_tenant() -> str:
    with connection.cursor() as cur:
        cur.execute("SELECT COALESCE(current_setting('app.tenant_id', true), '')")
        row = cur.fetchone()
    return str(row[0]) if row else ""


def bind_tenant(tenant_id: uuid.UUID) -> None:
    """SET LOCAL app.tenant_id inside the current transaction; refuses to switch tenants mid-transaction."""
    if not connection.in_atomic_block:
        raise RuntimeError("bind_tenant must run inside transaction.atomic()")
    bound = current_tenant()
    if bound and bound != str(tenant_id):
        raise TenantMismatchError(f"transaction bound to tenant {bound}, not {tenant_id}")
    if not bound:
        with connection.cursor() as cur:
            cur.execute("SELECT set_config('app.tenant_id', %s, true)", [str(tenant_id)])


@contextmanager
def tenant_transaction(tenant_id: uuid.UUID) -> Iterator[None]:
    """transaction.atomic() with app.tenant_id bound for its duration."""
    with transaction.atomic():
        bind_tenant(tenant_id)
        yield
