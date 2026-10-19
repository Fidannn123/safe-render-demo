"""Concurrent submits of the same end_to_end_id create exactly one transfer and debit once."""

from __future__ import annotations

import json
import threading
from typing import Any

import pytest
from django.db import connection
from django.test import Client

from apps.accounts.models import Account, Entry
from apps.transfers.models import Receipt, Transfer
from tests.conftest import Keys, SignedClient, new_account, transfer_request

pytestmark = pytest.mark.django_db(transaction=True)

THREADS = 8


def _race(keys: Keys, payloads: list[dict[str, Any]]) -> list[tuple[int, Any]]:
    barrier = threading.Barrier(len(payloads))
    results: list[tuple[int, Any]] = []
    lock = threading.Lock()

    def worker(payload: dict[str, Any]) -> None:
        try:
            client = SignedClient(Client(), keys.core)
            barrier.wait(timeout=30)
            response = client.post("/v1/transfers", payload)
            with lock:
                results.append((response.status_code, json.loads(response.content)))
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return results


def test_concurrent_same_e2e_creates_one_transfer(keys: Keys) -> None:
    acct = new_account(balance=1_000_000)
    req = transfer_request(acct, 100_000)
    results = _race(keys, [req] * THREADS)
    statuses = sorted(code for code, _ in results)
    assert statuses == [200] * (THREADS - 1) + [201]
    assert len({body["transfer_id"] for _, body in results}) == 1
    assert Transfer.objects.filter(end_to_end_id=req["end_to_end_id"]).count() == 1
    assert Receipt.objects.count() == 1
    assert Entry.objects.filter(account=acct, direction="DEBIT").count() == 1
    assert Account.objects.get(pk=acct.pk).balance_minor == 900_000


def test_concurrent_same_e2e_different_debtors(keys: Keys) -> None:
    """Different debtor rows are locked independently; UNIQUE(end_to_end_id) still decides."""
    a, b = new_account(balance=500_000), new_account(balance=500_000)
    req_a = transfer_request(a, 1_000)
    req_b = {**req_a, "debtor_account_id": str(b.account_id)}
    results = _race(keys, [req_a, req_b] * 3)
    assert Transfer.objects.count() == 1
    winner = Transfer.objects.get()
    codes = sorted(code for code, _ in results)
    assert codes.count(201) == 1
    assert codes.count(200) == 2
    assert codes.count(409) == 3
    assert Entry.objects.filter(direction="DEBIT").count() == 1
    total = Account.objects.get(pk=a.pk).balance_minor + Account.objects.get(pk=b.pk).balance_minor
    assert total == 999_000
    assert str(winner.debtor_id) in {str(a.account_id), str(b.account_id)}


def test_concurrent_distinct_transfers_never_overdraw(keys: Keys) -> None:
    acct = new_account(balance=300_000)
    results = _race(keys, [transfer_request(acct, 100_000) for _ in range(THREADS)])
    settled = [b for c, b in results if c == 201 and b["status"] == "SETTLED"]
    failed = [b for c, b in results if c == 201 and b["status"] == "FAILED"]
    assert len(settled) == 3
    assert len(failed) == THREADS - 3
    assert all(b["receipt"]["failure_reason"] == "INSUFFICIENT_FUNDS" for b in failed)
    assert Account.objects.get(pk=acct.pk).balance_minor == 0
