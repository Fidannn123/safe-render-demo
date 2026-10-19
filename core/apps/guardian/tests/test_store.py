"""guardian_decision table: append-only, UNIQUE(nonce), FORCE RLS, stored inputs replay (PostgreSQL)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from django.conf import settings
from django.db import DatabaseError, IntegrityError, connection, transaction

from apps.guardian.decide import IssuedDecision, issue_decision, verify_decision
from apps.guardian.models import GuardianDecision
from apps.guardian.policy import load_policy
from apps.guardian.recheck import load_bound_inputs
from apps.guardian.store import load_decision, record_decision, set_tenant
from apps.keystore.file_store import FileKeyStore

from .factories import TENANT, base, build

OTHER_TENANT = "99999999-8888-4777-8666-555555555555"


@pytest.fixture
def keystore(tmp_path: Path) -> FileKeyStore:
    return FileKeyStore(tmp_path / "keystore")


@pytest.fixture
def issued(keystore: FileKeyStore) -> IssuedDecision:
    return issue_decision(build(base()), load_policy(), keystore)


def count_visible() -> int:
    with connection.cursor() as cur:
        cur.execute("SELECT count(*) FROM guardian_decision")
        return int(cur.fetchone()[0])


@pytest.mark.django_db
def test_record_and_reload_verifies(issued: IssuedDecision, keystore: FileKeyStore) -> None:
    row = record_decision(issued)
    set_tenant(TENANT)
    stored = GuardianDecision.objects.get(pk=row.pk)
    decision = load_decision(stored)
    assert decision == issued.decision
    assert verify_decision(decision, keystore.public_key(stored.key_id))
    raw = stored.inputs_canonical.encode("utf-8")
    assert hashlib.sha256(raw).hexdigest() == stored.inputs_sha256
    assert load_bound_inputs(raw, stored.inputs_sha256) == build(base())


@pytest.mark.django_db
def test_rls_hides_rows_from_other_tenants(issued: IssuedDecision) -> None:
    record_decision(issued)
    set_tenant(TENANT)
    assert count_visible() == 1
    set_tenant(OTHER_TENANT)
    assert count_visible() == 0
    set_tenant("")
    assert count_visible() == 0


@pytest.mark.django_db
def test_nonce_is_unique(issued: IssuedDecision, keystore: FileKeyStore) -> None:
    record_decision(issued)
    second = issue_decision(build(base()), load_policy(), keystore)
    clash = IssuedDecision(
        decision=second.decision.model_copy(update={"nonce": issued.decision.nonce}),
        canonical_inputs=second.canonical_inputs,
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        record_decision(clash)


@pytest.mark.django_db
def test_rows_are_append_only_even_for_owner(issued: IssuedDecision) -> None:
    record_decision(issued)
    set_tenant(TENANT)
    for sql in ("UPDATE guardian_decision SET score = 0", "DELETE FROM guardian_decision"):
        with (
            pytest.raises(DatabaseError, match="append-only"),
            transaction.atomic(),
            connection.cursor() as c,
        ):
            c.execute(sql)
    assert count_visible() == 1


@pytest.mark.django_db
def test_tier_must_match_verdict(issued: IssuedDecision) -> None:
    bad = IssuedDecision(
        decision=issued.decision.model_copy(update={"tier": "T3"}), canonical_inputs=issued.canonical_inputs
    )
    with pytest.raises(IntegrityError), transaction.atomic():
        record_decision(bad)


def test_table_is_declared_append_only() -> None:
    assert GuardianDecision._meta.db_table == "guardian_decision"
    assert "guardian_decision" in settings.SAFEPAY_APPEND_ONLY_TABLES


# ------------------------------------------------------------------ runtime APP role (committed rows)
@pytest.fixture
def app() -> Iterator[psycopg.Connection[Any]]:
    cfg = connection.settings_dict
    conn = psycopg.connect(
        host=cfg["HOST"],
        port=cfg["PORT"],
        dbname=cfg["NAME"],
        user=settings.SAFEPAY_DB_APP_ROLE,
        password=settings.SAFEPAY_DB_APP_PASSWORD,
        autocommit=True,
        connect_timeout=15,
    )
    try:
        yield conn
    finally:
        conn.close()


@pytest.mark.django_db(transaction=True)
def test_app_role_can_insert_and_read_but_never_mutate(
    issued: IssuedDecision, app: psycopg.Connection[Any]
) -> None:
    from safepay_core.db_policy import apply_db_policy  # noqa: PLC0415

    assert "guardian_decision" not in apply_db_policy()
    record_decision(issued)  # committed (transactional test)
    app.execute("SELECT set_config('app.tenant_id', %s, false)", (TENANT,))
    assert app.execute("SELECT count(*) FROM guardian_decision").fetchone() == (1,)
    for sql in (
        "UPDATE guardian_decision SET score = 0",
        "DELETE FROM guardian_decision",
        "TRUNCATE guardian_decision",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            app.execute(sql)
    app.execute("SELECT set_config('app.tenant_id', '', false)")
    assert app.execute("SELECT count(*) FROM guardian_decision").fetchone() == (0,)
