"""Append-only intel tables (D-033) on the REAL tables, admission control command, IBAN helpers."""

from __future__ import annotations

import base64
import io
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.management import CommandError, call_command
from django.db import connection

from apps.members.models import Member, MemberAccount, MemberKey
from apps.oprf.payee import iban_valid, normalise_iban, payee_input

from .conftest import _IBANS, OTHER_PAYEE_IBAN, PAYEE_IBAN, MemberHandle, raw_pub
from .test_db_roles import app_connect

APPEND_ONLY = ["reports_receipt", "snapshots_snapshot"]


@pytest.fixture
def populated(make_member: Callable[..., MemberHandle], make_receipt: Callable[..., dict[str, Any]]) -> None:
    m = make_member()
    assert m.call("POST", "/v1/reports", m.report(make_receipt(m))).status_code == 201
    epoch = json.loads(m.call("GET", "/v1/epochs/current").content)["epoch"]
    assert m.call("GET", f"/v1/snapshots/{epoch}").status_code == 200


@pytest.fixture
def app() -> Iterator[psycopg.Connection[Any]]:
    conn = app_connect()
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("table", APPEND_ONLY)
def test_app_role_cannot_mutate_append_only_tables(
    populated: None, app: psycopg.Connection[Any], table: str
) -> None:
    assert app.execute(f"SELECT count(*) FROM {table}").fetchone() == (1,)  # noqa: S608
    for stmt in (f"UPDATE {table} SET id = id", f"DELETE FROM {table}", f"TRUNCATE {table}"):  # noqa: S608
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute(stmt)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("table", APPEND_ONLY)
def test_owner_role_cannot_update_append_only_tables(populated: None, table: str) -> None:
    """The trigger raises for every role, including the owner the tests run as."""
    from django.db import DatabaseError  # noqa: PLC0415

    with pytest.raises(DatabaseError, match="append-only"), connection.cursor() as cur:
        cur.execute(f"UPDATE {table} SET id = id")  # noqa: S608


@pytest.mark.django_db(transaction=True)
def test_app_role_can_retract_reports(populated: None, app: psycopg.Connection[Any]) -> None:
    app.execute("UPDATE reports_report SET retracted_at = now()")


@pytest.mark.django_db
def test_create_member_command() -> None:
    key = Ed25519PrivateKey.generate()
    account = str(uuid.uuid4())
    out = io.StringIO()
    args = [
        "--name",
        "Twin Retail (SYNTHETIC)",
        "--key-id",
        "member-twin1:1",
        "--public-key",
        base64.b64encode(raw_pub(key)).decode(),
        "--account",
        f"{account}:GB33 BUKB 2020 1555 5555 55",
    ]
    call_command("create_member", *args, stdout=out)
    member_id = json.loads(out.getvalue())["member_id"]
    member = Member.objects.get(id=member_id)
    assert member.status == Member.Status.ACTIVE
    assert bytes(MemberKey.objects.get(key_id="member-twin1:1").public_key) == raw_pub(key)
    assert MemberAccount.objects.get(account_id=account).iban == "GB33BUKB20201555555555"
    call_command("create_member", *args, "--member-id", member_id, stdout=io.StringIO())  # idempotent
    with pytest.raises(CommandError):
        call_command("create_member", *args[:-1], f"{account}:GB00INVALID000000000", stdout=io.StringIO())
    other = base64.b64encode(raw_pub(Ed25519PrivateKey.generate())).decode()
    with pytest.raises(CommandError):
        call_command(
            "create_member",
            "--name",
            "x",
            "--key-id",
            "member-twin1:1",
            "--public-key",
            other,
            stdout=io.StringIO(),
        )


@pytest.mark.parametrize("iban", [PAYEE_IBAN, OTHER_PAYEE_IBAN, *_IBANS])
def test_fixture_ibans_are_valid(iban: str) -> None:
    assert iban_valid(iban)


def test_iban_and_payee_input() -> None:
    assert normalise_iban(" gb82 west 1234 5698 7654 32 ") == PAYEE_IBAN
    assert not iban_valid("GB82WEST12345698765433")
    assert payee_input("GB", "gb82 west 1234 5698 7654 32") == b"safepay/v1/payee|GB|GB82WEST12345698765432"
    with pytest.raises(ValueError, match="invalid"):
        payee_input("GB", "GB82WEST12345698765433")
