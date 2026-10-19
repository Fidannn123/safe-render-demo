"""Property tests over random complete payment lifecycles and outcomes."""

from __future__ import annotations

import uuid

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from .helpers import (
    approve,
    assert_ledger_invariants,
    bal,
    bank_fail,
    decide,
    deliver,
    fund,
    proposed,
    reconcile,
    reject,
    settle,
    veto,
)


@pytest.mark.django_db(transaction=True)
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            st.integers(1, 500), st.sampled_from(["settle", "fail", "deny", "reject", "approve", "veto"])
        ),
        min_size=1,
        max_size=8,
    )
)
def test_random_lifecycles_keep_database_invariants(events: list[tuple[int, str]]) -> None:
    tenant = uuid.uuid4()
    opening, paid = 10_000, 0
    fund(tenant, opening)
    for index, (amount, outcome) in enumerate(events):
        payment = proposed(tenant, amount, ref=f"PROP-{index}")
        if outcome == "deny":
            decide(payment, "DENY")
        elif outcome == "reject":
            reject(decide(payment, "HOLD"), "fraud_or_invalid")
        elif outcome == "veto":
            veto(decide(payment, "GUARDED"))
        else:
            reserved = approve(decide(payment, "HOLD")) if outcome == "approve" else decide(payment, "ALLOW")
            submitted = deliver(reserved)
            if outcome == "fail":
                bank_fail(submitted)
            else:
                reconcile(settle(submitted))
                paid += amount
        assert bal(tenant, "Bank:available") == opening - paid
        assert bal(tenant, "Bank:reserved") == 0
        assert bal(tenant, "Bank:in_transit") == 0
        assert_ledger_invariants(tenant)
