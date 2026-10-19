# ruff: noqa: PT012
"""App-role restrictions on the REAL M1 tables (pattern: core/tests/test_db_roles.py, D-013/D-033)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from django.conf import settings
from django.db import connection

from apps.ledger.models import Account
from apps.ledger.tenancy import tenant_transaction
from apps.payments.idempotency import run_idempotent
from apps.payments.models import OutboxMessage

from .helpers import decide, fund, proposed

pytestmark = pytest.mark.django_db(transaction=True)

APPEND_ONLY = ["ledger_posting", "ledger_journal_entry", "audit_event"]
TENANT_TABLES = [
    "ledger_account",
    "ledger_journal_entry",
    "ledger_posting",
    "ledger_balance",
    "audit_event",
    "payments_payment",
    "payments_tenant_limits",
    "payments_idempotency_key",
]
TRIGGERS = {
    "ledger_posting": ["safepay_append_only", "ledger_posting_balanced", "ledger_apply_posting"],
    "ledger_journal_entry": ["safepay_append_only", "ledger_entry_balanced"],
    "audit_event": ["safepay_append_only", "audit_event_link_check"],
    "ledger_balance": ["ledger_balance_guard"],
    "ledger_account": ["ledger_account_immutable"],
}


def app_connect() -> psycopg.Connection[Any]:
    cfg = connection.settings_dict
    return psycopg.connect(
        host=cfg["HOST"],
        port=cfg["PORT"],
        dbname=cfg["NAME"],
        user=settings.SAFEPAY_DB_APP_ROLE,
        password=settings.SAFEPAY_DB_APP_PASSWORD,
        autocommit=True,
        connect_timeout=15,
    )


def set_tenant(conn: psycopg.Connection[Any], tenant: uuid.UUID | None) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (str(tenant) if tenant else "",))


@pytest.fixture
def tenant() -> uuid.UUID:
    t = uuid.uuid4()
    fund(t, 10_000)
    decide(proposed(t, 1_000), "ALLOW")  # postings, audit events, payment, outbox
    run_idempotent(t, "rls-test-key-0001", "0" * 64, lambda: (201, {"synthetic": True}))
    return t


@pytest.fixture
def app(tenant: uuid.UUID) -> Iterator[psycopg.Connection[Any]]:
    conn = app_connect()
    set_tenant(conn, tenant)
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.parametrize("table", APPEND_ONLY)
@pytest.mark.parametrize("verb", ["UPDATE {t} SET tenant_id = tenant_id", "DELETE FROM {t}", "TRUNCATE {t}"])
def test_app_cannot_mutate_append_only(app: psycopg.Connection[Any], table: str, verb: str) -> None:
    before = app.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608 - fixed table names
    assert before is not None
    assert before[0] > 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute(verb.format(t=table))
    assert app.execute(f"SELECT count(*) FROM {table}").fetchone() == before  # noqa: S608


@pytest.mark.parametrize(("table", "trigger"), [(t, g) for t, gs in TRIGGERS.items() for g in gs])
def test_app_cannot_disable_triggers(app: psycopg.Connection[Any], table: str, trigger: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="must be owner"):
        app.execute(f"ALTER TABLE {table} DISABLE TRIGGER {trigger}")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="must be owner"):
        app.execute(f"ALTER TABLE {table} DISABLE TRIGGER ALL")


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_app_sees_zero_rows_without_tenant(
    app: psycopg.Connection[Any], tenant: uuid.UUID, table: str
) -> None:
    sql = f"SELECT count(*) FROM {table}"  # noqa: S608 - fixed table names
    row = app.execute(sql).fetchone()
    assert row is not None
    assert row[0] > 0
    set_tenant(app, None)
    assert app.execute(sql).fetchone() == (0,)
    set_tenant(app, uuid.uuid4())
    assert app.execute(sql).fetchone() == (0,)


@pytest.mark.parametrize("table", TENANT_TABLES)
def test_rls_forced(table: str) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = %s", [table])
        assert cur.fetchone() == (True, True)


def test_app_cannot_write_balances_or_accounts(app: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="ledger_balance"):
        app.execute("UPDATE ledger_balance SET balance = balance + 1000000")
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="ledger_account"):
        app.execute("UPDATE ledger_account SET non_negative = false")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute("DELETE FROM ledger_balance")


def test_app_can_post_balanced_entry_and_trigger_updates_balance(
    app: psycopg.Connection[Any], tenant: uuid.UUID
) -> None:
    with tenant_transaction(tenant):
        avail = Account.objects.get(tenant_id=tenant, code="Bank:available").id
        reserved = Account.objects.get(tenant_id=tenant, code="Bank:reserved").id
    entry = uuid.uuid4()
    with app.transaction():
        app.execute(
            "INSERT INTO ledger_journal_entry (id, tenant_id, template, memo, created_at) "
            "VALUES (%s, %s, 'reserve', '', now())",
            (entry, tenant),
        )
        for acct, amt in ((reserved, 100), (avail, -100)):
            app.execute(
                "INSERT INTO ledger_posting (id, tenant_id, entry_id, account_id, amount_minor, currency) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'USD')",
                (tenant, entry, acct, amt),
            )
    row = app.execute(
        "SELECT b.balance FROM ledger_balance b JOIN ledger_account a ON a.id = b.account_id "
        "WHERE a.code = 'Bank:available'"
    ).fetchone()
    assert row == (8_900,)


def test_app_unbalanced_entry_rejected_at_commit(app: psycopg.Connection[Any], tenant: uuid.UUID) -> None:
    with tenant_transaction(tenant):
        avail = Account.objects.get(tenant_id=tenant, code="Bank:available").id
    entry = uuid.uuid4()
    with pytest.raises(psycopg.errors.CheckViolation, match="does not balance"), app.transaction():
        app.execute(
            "INSERT INTO ledger_journal_entry (id, tenant_id, template, memo, created_at) "
            "VALUES (%s, %s, 'manual', '', now())",
            (entry, tenant),
        )
        for amt in (-1, -1):
            app.execute(
                "INSERT INTO ledger_posting (id, tenant_id, entry_id, account_id, amount_minor, currency) "
                "VALUES (gen_random_uuid(), %s, %s, %s, %s, 'USD')",
                (tenant, entry, avail, amt),
            )


def test_outbox_is_visible_to_worker_without_tenant(tenant: uuid.UUID) -> None:
    conn = app_connect()
    try:
        row = conn.execute("SELECT count(*) FROM payments_outbox WHERE tenant_id = %s", (tenant,)).fetchone()
        assert row == (OutboxMessage.objects.filter(tenant_id=tenant).count(),) == (1,)
    finally:
        conn.close()
