"""Database role tests (DECISIONS.md D-013/D-033): the runtime APP role is least-privileged.

Tests run as the OWNER role (creates the test DB, migrates, applies db_policy); each check below opens
a separate connection as the APP role, exactly like the web and worker containers do.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from django.conf import settings
from django.db import connection

pytestmark = pytest.mark.django_db(transaction=True)

OTHER_DATABASES = ["safepay_intel", "safepay_bank"]


def app_connect(dbname: str | None = None) -> psycopg.Connection[Any]:
    cfg = connection.settings_dict
    return psycopg.connect(
        host=cfg["HOST"],
        port=cfg["PORT"],
        dbname=dbname or cfg["NAME"],
        user=settings.SAFEPAY_DB_APP_ROLE,
        password=settings.SAFEPAY_DB_APP_PASSWORD,
        autocommit=True,
        connect_timeout=15,
    )


@pytest.fixture
def app() -> Iterator[psycopg.Connection[Any]]:
    conn = app_connect()
    try:
        yield conn
    finally:
        conn.close()


def test_app_role_attributes(app: psycopg.Connection[Any]) -> None:
    row = app.execute(
        "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication FROM pg_roles "
        "WHERE rolname = current_user"
    ).fetchone()
    assert row == (False, False, False, False, False)


def test_app_role_owns_nothing(app: psycopg.Connection[Any]) -> None:
    row = app.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND pg_get_userbyid(c.relowner) = current_user"
    ).fetchone()
    assert row == (0,)


def test_app_cannot_create_tables(app: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute("CREATE TABLE should_not_exist (i int)")


def test_app_cannot_write_migrations_table(app: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute("INSERT INTO django_migrations (app, name, applied) VALUES ('x', 'y', now())")


@pytest.mark.parametrize("other", OTHER_DATABASES)
def test_app_cannot_connect_to_other_service_database(other: str) -> None:
    with pytest.raises(psycopg.OperationalError, match=r"permission denied|not have CONNECT"):
        app_connect(other).close()


def test_app_cannot_connect_to_maintenance_database() -> None:
    with pytest.raises(psycopg.OperationalError, match=r"permission denied|not have CONNECT"):
        app_connect("postgres").close()


TENANT_A = "11111111-1111-4111-8111-111111111111"
TENANT_B = "22222222-2222-4222-8222-222222222222"


def set_tenant(conn: psycopg.Connection[Any], tenant: str | None) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, false)", (tenant or "",))


def test_rls_zero_rows_without_tenant(app: psycopg.Connection[Any]) -> None:
    set_tenant(app, TENANT_A)
    app.execute(
        "INSERT INTO dbprobe_tenant_row (id, tenant_id, note) VALUES (gen_random_uuid(), %s, 'a')",
        (TENANT_A,),
    )
    set_tenant(app, None)
    assert app.execute("SELECT count(*) FROM dbprobe_tenant_row").fetchone() == (0,)
    set_tenant(app, TENANT_B)
    assert app.execute("SELECT count(*) FROM dbprobe_tenant_row").fetchone() == (0,)
    set_tenant(app, TENANT_A)
    assert app.execute("SELECT count(*) FROM dbprobe_tenant_row").fetchone() == (1,)


def test_rls_rejects_writes_for_other_tenant(app: psycopg.Connection[Any]) -> None:
    set_tenant(app, TENANT_A)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute(
            "INSERT INTO dbprobe_tenant_row (id, tenant_id, note) VALUES (gen_random_uuid(), %s, 'x')",
            (TENANT_B,),
        )


def test_rls_is_forced_for_the_owner_too() -> None:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname = 'dbprobe_tenant_row'"
        )
        assert cur.fetchone() == (True, True)


@pytest.fixture
def ledger_row(app: psycopg.Connection[Any]) -> psycopg.Connection[Any]:
    set_tenant(app, TENANT_A)
    app.execute(
        "INSERT INTO dbprobe_append_only (id, tenant_id, amount_minor) VALUES (gen_random_uuid(), %s, 100)",
        (TENANT_A,),
    )
    return app


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE dbprobe_append_only SET amount_minor = 1",
        "DELETE FROM dbprobe_append_only",
        "TRUNCATE dbprobe_append_only",
    ],
)
def test_app_cannot_mutate_append_only_rows(ledger_row: psycopg.Connection[Any], sql: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        ledger_row.execute(sql)
    assert ledger_row.execute("SELECT amount_minor FROM dbprobe_append_only").fetchone() == (100,)


def test_app_cannot_disable_trigger(ledger_row: psycopg.Connection[Any]) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="must be owner"):
        ledger_row.execute("ALTER TABLE dbprobe_append_only DISABLE TRIGGER safepay_append_only")


def test_trigger_blocks_even_the_owner(ledger_row: psycopg.Connection[Any]) -> None:
    from django.db import DatabaseError  # noqa: PLC0415

    with connection.cursor() as cur:
        cur.execute("SELECT set_config('app.tenant_id', %s, false)", [TENANT_A])
        for sql in ("UPDATE dbprobe_append_only SET amount_minor = 1", "DELETE FROM dbprobe_append_only"):
            with pytest.raises(DatabaseError, match="append-only"):
                cur.execute(sql)


def test_session_rows_are_deletable_by_app(app: psycopg.Connection[Any]) -> None:
    """The one documented DELETE exception (logout)."""
    app.execute("DELETE FROM django_session WHERE false")


def test_declared_append_only_tables_report() -> None:
    """Lists declared tables not created yet; becomes empty once M1/M2/M9 add them (G2 gate)."""
    from safepay_core.db_policy import apply_db_policy  # noqa: PLC0415

    missing = apply_db_policy()
    assert "dbprobe_append_only" not in missing
    assert set(missing) <= set(settings.SAFEPAY_APPEND_ONLY_TABLES)
