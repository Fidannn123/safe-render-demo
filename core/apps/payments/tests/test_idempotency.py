"""Idempotency-Key store helper (architecture.md 2.5)."""

from __future__ import annotations

import uuid

import pytest

from apps.ledger.tenancy import tenant_transaction
from apps.payments.errors import ProblemError
from apps.payments.idempotency import request_sha256, run_idempotent
from apps.payments.models import IdempotencyKey

pytestmark = pytest.mark.django_db(transaction=True)
KEY = "c2b7a1d4-0000-4000-8000-000000000001"


def test_first_call_runs_and_stores() -> None:
    tenant, calls = uuid.uuid4(), []
    h = request_sha256("POST", "/api/v1/payments", b'{"a":1}')

    def handler() -> tuple[int, object]:
        calls.append(1)
        return 201, {"payment_id": "p1"}

    first = run_idempotent(tenant, KEY, h, handler)
    again = run_idempotent(tenant, KEY, h, handler)
    assert (first.status, first.body, first.replayed) == (201, {"payment_id": "p1"}, False)
    assert (again.status, again.body, again.replayed) == (201, {"payment_id": "p1"}, True)
    assert calls == [1]


def test_different_body_is_mismatch() -> None:
    tenant = uuid.uuid4()
    run_idempotent(tenant, KEY, request_sha256("POST", "/x", b"1"), lambda: (200, {}))
    with pytest.raises(ProblemError) as err:
        run_idempotent(tenant, KEY, request_sha256("POST", "/x", b"2"), lambda: (200, {}))
    assert err.value.code == "IDEMPOTENCY_MISMATCH"


def test_keys_are_per_tenant() -> None:
    h = request_sha256("POST", "/x", b"1")
    a = run_idempotent(uuid.uuid4(), KEY, h, lambda: (200, {"t": "a"}))
    b = run_idempotent(uuid.uuid4(), KEY, h, lambda: (200, {"t": "b"}))
    assert not a.replayed
    assert not b.replayed


def test_handler_failure_rolls_back_key() -> None:
    tenant = uuid.uuid4()
    h = request_sha256("POST", "/x", b"1")

    def boom() -> tuple[int, object]:
        raise ProblemError("CAP_EXCEEDED")

    with pytest.raises(ProblemError):
        run_idempotent(tenant, KEY, h, boom)
    with tenant_transaction(tenant):
        assert not IdempotencyKey.objects.filter(key=KEY).exists()
    assert run_idempotent(tenant, KEY, h, lambda: (201, {})).status == 201


@pytest.mark.parametrize(
    ("key", "code"),
    [
        (None, "IDEMPOTENCY_KEY_REQUIRED"),
        ("", "IDEMPOTENCY_KEY_REQUIRED"),
        ("short", "VALIDATION_FAILED"),
        ("x" * 17 + " ", "VALIDATION_FAILED"),
    ],
)
def test_key_format(key: str | None, code: str) -> None:
    with pytest.raises(ProblemError) as err:
        run_idempotent(uuid.uuid4(), key, "0" * 64, lambda: (200, {}))
    assert err.value.code == code


def test_request_hash_binds_method_path_body() -> None:
    base = request_sha256("POST", "/a", b"{}")
    assert base != request_sha256("PUT", "/a", b"{}")
    assert base != request_sha256("POST", "/b", b"{}")
    assert base != request_sha256("POST", "/a", b"{ }")
    assert base == request_sha256("post", "/a", b"{}")
