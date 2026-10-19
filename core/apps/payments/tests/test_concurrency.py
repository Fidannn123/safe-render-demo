"""Concurrent reservations cannot overdraw Bank:available (two threads, real transactions)."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

import pytest
from django.db import IntegrityError, connection

from apps.ledger.service import post_template
from apps.ledger.tenancy import tenant_transaction
from apps.payments.errors import ProblemError
from apps.payments.models import Payment

from .helpers import assert_ledger_invariants, bal, decide, fund, proposed

pytestmark = pytest.mark.django_db(transaction=True)


def run_concurrently(fns: list[Callable[[], object]]) -> list[object]:
    barrier = threading.Barrier(len(fns))
    results: list[object] = [None] * len(fns)

    def worker(i: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[i] = fns[i]()
        except Exception as exc:
            results[i] = exc
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(len(fns))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    return results


def test_two_concurrent_reserves_cannot_overdraw() -> None:
    tenant = uuid.uuid4()
    fund(tenant, 1_000)
    a, b = proposed(tenant, 600), proposed(tenant, 600)
    results = run_concurrently([lambda: decide(a, "ALLOW"), lambda: decide(b, "ALLOW")])
    ok = [r for r in results if isinstance(r, Payment)]
    errors = [r for r in results if isinstance(r, ProblemError)]
    assert len(ok) == 1, results
    assert len(errors) == 1
    assert errors[0].code == "FUNDS_UNAVAILABLE"
    assert bal(tenant, "Bank:available") == 400
    assert bal(tenant, "Bank:reserved") == 600
    assert_ledger_invariants(tenant)


def test_raw_concurrent_postings_cannot_overdraw_without_app_lock() -> None:
    """Even without the tenant-limits lock, the balance row lock + CHECK stop the second reserve."""
    tenant = uuid.uuid4()
    fund(tenant, 1_000)

    def reserve() -> str:
        with tenant_transaction(tenant):
            post_template(tenant, "reserve", 700, "USD")
        return "ok"

    results = run_concurrently([reserve, reserve])
    assert results.count("ok") == 1, results
    assert sum(isinstance(r, IntegrityError) for r in results) == 1
    assert bal(tenant, "Bank:available") == 300
    assert_ledger_invariants(tenant)
