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

OTHER_DATABASES = ["safepay_core", "safepay_intel"]


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


# ------------------------------------------------------------------ M3 append-only tables (D-033)
APPEND_ONLY = ["transfers_receipt", "statements_statement"]


@pytest.fixture
def issued_documents() -> None:
    """One real receipt and one real statement, issued through the services (as the owner)."""
    from apps.statements.services import get_statement  # noqa: PLC0415
    from apps.transfers.services import submit_transfer  # noqa: PLC0415
    from safepay_bank import clock  # noqa: PLC0415
    from tests.conftest import new_account, transfer_request  # noqa: PLC0415

    account = new_account(balance=10_000)
    submit_transfer(transfer_request(account, 1_000))
    get_statement(account.account_id, clock.now().date())


def test_declared_append_only_tables_exist() -> None:
    from safepay_bank.db_policy import apply_db_policy  # noqa: PLC0415

    assert apply_db_policy() == []
    assert set(APPEND_ONLY) == set(settings.SAFEPAY_APPEND_ONLY_TABLES)


@pytest.mark.parametrize("table", APPEND_ONLY)
@pytest.mark.parametrize("verb", ["UPDATE {t} SET created_at = now()", "DELETE FROM {t}", "TRUNCATE {t}"])
def test_app_cannot_mutate_append_only_rows(
    issued_documents: None, app: psycopg.Connection[Any], table: str, verb: str
) -> None:
    before = app.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608
    assert before == (1,)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute(verb.format(t=table))
    assert app.execute(f"SELECT count(*) FROM {table}").fetchone() == before  # noqa: S608


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_app_cannot_disable_append_only_trigger(app: psycopg.Connection[Any], table: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="must be owner"):
        app.execute(f"ALTER TABLE {table} DISABLE TRIGGER safepay_append_only")


@pytest.mark.parametrize("table", APPEND_ONLY)
def test_trigger_blocks_even_the_owner(issued_documents: None, table: str) -> None:
    from django.db import DatabaseError, transaction  # noqa: PLC0415

    for sql in (f"UPDATE {table} SET created_at = now()", f"DELETE FROM {table}"):  # noqa: S608
        with (
            pytest.raises(DatabaseError, match="append-only"),
            transaction.atomic(),
            connection.cursor() as cur,
        ):
            cur.execute(sql)


def test_app_can_run_the_service(app: psycopg.Connection[Any]) -> None:
    """The runtime role has what the bank needs: insert receipts/statements, update balances, prune nonces."""
    for table in ("transfers_receipt", "statements_statement", "transfers_transfer", "accounts_entry"):
        row = app.execute(
            "SELECT has_table_privilege(current_user, %s, 'INSERT'), "
            "has_table_privilege(current_user, %s, 'SELECT')",
            (table, table),
        ).fetchone()
        assert row == (True, True), table
    assert app.execute(
        "SELECT has_table_privilege(current_user, 'accounts_account', 'UPDATE')"
    ).fetchone() == (True,)
    app.execute("DELETE FROM safepay_bank_requestnonce WHERE false")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        app.execute("DELETE FROM accounts_entry WHERE false")


def test_app_cannot_overdraw_an_account(app: psycopg.Connection[Any]) -> None:
    from tests.conftest import new_account  # noqa: PLC0415

    account = new_account(balance=100)
    with pytest.raises(psycopg.errors.CheckViolation):
        app.execute(
            "UPDATE accounts_account SET balance_minor = balance_minor - 101 WHERE account_id = %s",
            (account.account_id,),
        )
