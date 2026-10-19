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

OTHER_DATABASES = ["safepay_core", "safepay_bank"]


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
