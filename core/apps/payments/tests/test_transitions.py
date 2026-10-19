"""State machine executor: postings, audit events, outbox, guards, atomic chains (SYNTHETIC data)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.ledger.models import JournalEntry
from apps.ledger.tenancy import tenant_transaction
from apps.payments.errors import IllegalTransitionError, ProblemError
from apps.payments.models import OutboxMessage, Payment, TenantLimits
from apps.payments.service import transition
from apps.payments.spec import state_machine
from apps.payments.types import SYSTEM, TransitionContext

from .helpers import (
    APPROVER_1,
    APPROVER_2,
    BANK,
    VENDOR,
    approve,
    assert_ledger_invariants,
    bal,
    bank_fail,
    decide,
    deliver,
    extract,
    fund,
    propose,
    proposed,
    received,
    reconcile,
    reject,
    settle,
    veto,
)

pytestmark = pytest.mark.django_db(transaction=True)

AP = f"AP:{VENDOR}"


@pytest.fixture
def tenant() -> uuid.UUID:
    return uuid.uuid4()


def events(tenant: uuid.UUID, p: Payment) -> list[AuditEvent]:
    with tenant_transaction(tenant):
        return list(AuditEvent.objects.filter(tenant_id=tenant, subject_id=str(p.id)).order_by("seq"))


def outbox(p: Payment) -> list[OutboxMessage]:
    return list(OutboxMessage.objects.filter(payment_id=p.id))


def test_t1_happy_path_postings_audit_outbox(tenant: uuid.UUID) -> None:
    fund(tenant, 100_000)
    p = extract(received(tenant), 10_000)
    assert p.state == "EXTRACTED"
    assert p.invoice_accrued
    assert p.invoice_fingerprint
    assert bal(tenant, "Expense") == 10_000
    assert bal(tenant, AP) == 10_000
    p = decide(propose(p), "ALLOW")
    assert p.state == "RESERVED"  # atomic chain PROPOSED -> CLEARED -> RESERVED
    assert bal(tenant, "Bank:available") == 90_000
    assert bal(tenant, "Bank:reserved") == 10_000
    [msg] = outbox(p)
    assert msg.kind == "submit_transfer"
    assert msg.payload == {"payment_id": str(p.id), "end_to_end_id": str(p.id)}
    p = settle(deliver(p))
    assert bal(tenant, "Bank:reserved") == 0
    assert bal(tenant, "Bank:in_transit") == 0
    assert bal(tenant, AP) == 0
    p = reconcile(p)
    trail = [(e.data["from"], e.data["to"]) for e in events(tenant, p)]
    assert trail == [
        (None, "RECEIVED"),
        ("RECEIVED", "EXTRACTED"),
        ("EXTRACTED", "PROPOSED"),
        ("PROPOSED", "CLEARED"),
        ("CLEARED", "RESERVED"),
        ("RESERVED", "SUBMITTED"),
        ("SUBMITTED", "SETTLED"),
        ("SETTLED", "RECONCILED"),
    ]
    reserve_ev = events(tenant, p)[4]
    assert reserve_ev.data["outbox_id"] == str(msg.id)
    assert reserve_ev.data["journal_entry_id"]
    assert reserve_ev.actor == {"type": "system", "id": "system"}
    assert_ledger_invariants(tenant)


def test_unbound_vendor_posts_no_accrual(tenant: uuid.UUID) -> None:
    p = extract(received(tenant), 5_000, vendor=None)
    assert not p.invoice_accrued
    assert p.invoice_fingerprint is None
    with tenant_transaction(tenant):
        assert JournalEntry.objects.filter(payment_id=p.id).count() == 0


def test_hold_then_approve_reserves_in_one_chain(tenant: uuid.UUID) -> None:
    fund(tenant, 50_000)
    p = decide(proposed(tenant, 20_000), "HOLD")
    assert p.state == "HELD"
    assert p.tier == "T3"
    assert bal(tenant, "Bank:reserved") == 0
    p = approve(p)
    assert p.state == "RESERVED"
    assert bal(tenant, "Bank:reserved") == 20_000
    approval = next(e for e in events(tenant, p) if e.data["to"] == "APPROVED")
    assert approval.data["approver_ids"] == [APPROVER_1]
    assert approval.data["reason"]
    assert len(outbox(p)) == 1
    assert_ledger_invariants(tenant)


def test_two_approvers_required(tenant: uuid.UUID) -> None:
    fund(tenant, 5_000_000)
    p = decide(proposed(tenant, 1_500_000), "HOLD", approvals_required=2)
    with pytest.raises(ProblemError) as err:
        approve(p, (APPROVER_1,))
    assert err.value.code == "ILLEGAL_TRANSITION"
    with pytest.raises(ProblemError) as err:
        approve(p, (APPROVER_1, APPROVER_1))
    assert err.value.code == "ALREADY_APPROVED_BY_USER"
    assert approve(p, (APPROVER_1, APPROVER_2)).state == "RESERVED"


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"step_up_at": None}, "STEP_UP_REQUIRED"),
        ({"reason": " x "}, "REASON_REQUIRED"),
        ({"attested": frozenset({"payee_account_unchanged"})}, "HARD_RULE_FAILED"),
        ({"attested": frozenset({"hard_rules_pass"})}, "PAYEE_CHANGED"),
        ({"approver_ids": ("agent-1",)}, "SELF_APPROVAL"),
    ],
)
def test_approval_guards_leave_payment_held(tenant: uuid.UUID, overrides: dict[str, Any], code: str) -> None:
    fund(tenant, 50_000)
    p = decide(proposed(tenant, 1_000), "HOLD")
    n_events = len(events(tenant, p))
    with pytest.raises(ProblemError) as err:
        approve(p, **overrides)
    assert err.value.code == code
    with tenant_transaction(tenant):
        assert Payment.objects.get(id=p.id).state == "HELD"
    assert len(events(tenant, p)) == n_events


def test_stale_step_up_rejected(tenant: uuid.UUID) -> None:
    p = decide(proposed(tenant, 1_000), "HOLD")
    with pytest.raises(ProblemError) as err:
        approve(p, step_up_at=timezone.now() - timedelta(seconds=301))
    assert err.value.code == "STEP_UP_REQUIRED"


def test_warning_must_be_acknowledged(tenant: uuid.UUID) -> None:
    fund(tenant, 50_000)
    p = decide(proposed(tenant, 1_000), "HOLD", warnings=("CASH_BUFFER_BREACH",))
    with pytest.raises(ProblemError) as err:
        approve(p)
    assert err.value.code == "WARNING_NOT_ACKNOWLEDGED"
    p = approve(p, acknowledged_warnings=("CASH_BUFFER_BREACH",))
    approval = next(e for e in events(tenant, p) if e.data["to"] == "APPROVED")
    assert approval.data["acknowledged_warnings"] == ["CASH_BUFFER_BREACH"]


def test_frozen_tenant_cannot_approve_or_deliver(tenant: uuid.UUID) -> None:
    fund(tenant, 50_000)
    held = decide(proposed(tenant, 1_000), "HOLD")
    reserved = decide(proposed(tenant, 1_000), "ALLOW")
    with tenant_transaction(tenant):
        TenantLimits.objects.filter(tenant_id=tenant).update(frozen=True)
    with pytest.raises(ProblemError) as err:
        approve(held)
    assert err.value.code == "TENANT_FROZEN"
    with pytest.raises(ProblemError) as err:
        deliver(reserved)
    assert err.value.code == "TENANT_FROZEN"


def test_insufficient_funds_rolls_back_whole_chain(tenant: uuid.UUID) -> None:
    fund(tenant, 999)
    p = proposed(tenant, 1_000)
    n_events = len(events(tenant, p))
    with pytest.raises(ProblemError) as err:
        decide(p, "ALLOW")
    assert err.value.code == "FUNDS_UNAVAILABLE"
    with tenant_transaction(tenant):
        assert Payment.objects.get(id=p.id).state == "PROPOSED"
    assert len(events(tenant, p)) == n_events
    assert outbox(p) == []
    assert bal(tenant, "Bank:available") == 999
    assert_ledger_invariants(tenant)


def test_funds_guard_on_approval(tenant: uuid.UUID) -> None:
    fund(tenant, 500)
    p = decide(proposed(tenant, 1_000), "HOLD")
    with pytest.raises(ProblemError) as err:
        approve(p)
    assert err.value.code == "FUNDS_UNAVAILABLE"


def test_deny_voids_accrual_as_new_entry(tenant: uuid.UUID) -> None:
    p = decide(proposed(tenant, 7_000), "DENY")
    assert p.state == "DENIED"
    assert not p.invoice_accrued
    assert bal(tenant, "Expense") == 0
    assert bal(tenant, AP) == 0
    with tenant_transaction(tenant):
        entries = list(JournalEntry.objects.filter(payment_id=p.id).order_by("created_at"))
        assert [e.template for e in entries] == ["invoice_accepted", "invoice_voided"]
        assert entries[1].reverses_id == entries[0].id
    assert_ledger_invariants(tenant)


def test_reject_not_now_can_be_reproposed(tenant: uuid.UUID) -> None:
    p = reject(decide(proposed(tenant, 3_000), "HOLD"), "not_now")
    assert p.state == "REJECTED"
    assert p.reject_reason == "not_now"
    assert not p.payee_reportable
    assert bal(tenant, AP) == 3_000  # accrual stays open
    p = propose(p)
    assert p.state == "PROPOSED"
    assert p.reject_reason is None
    assert p.decision_id is None
    assert_ledger_invariants(tenant)


def test_reject_fraud_is_final_and_voids(tenant: uuid.UUID) -> None:
    p = reject(decide(proposed(tenant, 3_000), "HOLD"), "fraud_or_invalid")
    assert p.state == "REJECTED"
    assert p.payee_reportable
    assert not p.invoice_accrued
    assert bal(tenant, AP) == 0
    assert bal(tenant, "Expense") == 0
    with pytest.raises(IllegalTransitionError):
        propose(p)
    ev = events(tenant, p)[-1]
    assert ev.data["reject_reason"] == "fraud_or_invalid"
    assert ev.data["journal_entry_id"]
    assert_ledger_invariants(tenant)


def test_reject_reason_must_be_valid(tenant: uuid.UUID) -> None:
    p = decide(proposed(tenant, 3_000), "HOLD")
    with pytest.raises(ProblemError) as err:
        reject(p, "because")
    assert err.value.code == "VALIDATION_FAILED"


def test_duplicate_live_fingerprint_rejected(tenant: uuid.UUID) -> None:
    proposed(tenant, 4_000, ref="INV-77")
    with pytest.raises(ProblemError) as err:
        extract(received(tenant), 4_000, ref="inv 77")
    assert err.value.code == "DUPLICATE_PAYMENT"


def test_fingerprint_freed_after_fraud_rejection(tenant: uuid.UUID) -> None:
    reject(decide(proposed(tenant, 4_000, ref="INV-78"), "HOLD"), "fraud_or_invalid")
    assert extract(received(tenant), 4_000, ref="INV-78").state == "EXTRACTED"


def test_fingerprint_still_held_after_not_now(tenant: uuid.UUID) -> None:
    reject(decide(proposed(tenant, 4_000, ref="INV-79"), "HOLD"), "not_now")
    with pytest.raises(ProblemError) as err:
        extract(received(tenant), 4_000, ref="INV-79")
    assert err.value.code == "DUPLICATE_PAYMENT"


def test_bank_failure_releases_in_transit(tenant: uuid.UUID) -> None:
    fund(tenant, 10_000)
    p = bank_fail(deliver(decide(proposed(tenant, 2_500), "ALLOW")))
    assert p.state == "FAILED"
    assert bal(tenant, "Bank:available") == 10_000
    assert bal(tenant, "Bank:in_transit") == 0
    assert bal(tenant, AP) == 2_500  # the debt is still owed (D-004)
    assert_ledger_invariants(tenant)


def test_receipt_signature_required(tenant: uuid.UUID) -> None:
    fund(tenant, 10_000)
    p = deliver(decide(proposed(tenant, 2_500), "ALLOW"))
    with pytest.raises(ProblemError) as err:
        transition(p, "SETTLED", "bank_receipt_settled", BANK, TransitionContext())
    assert err.value.code == "VALIDATION_FAILED"


def test_t2_cooloff_and_veto(tenant: uuid.UUID) -> None:
    fund(tenant, 10_000)
    now = timezone.now()
    p = decide(proposed(tenant, 2_000), "GUARDED", now=now)
    assert p.state == "RESERVED"
    assert p.tier == "T2"
    assert p.cooloff_until is not None
    [msg] = outbox(p)
    assert msg.available_at == p.cooloff_until
    with pytest.raises(ProblemError) as err:
        deliver(p, now=now + timedelta(seconds=10))
    assert err.value.code == "ILLEGAL_TRANSITION"
    p = veto(p, now=now + timedelta(seconds=10))
    assert p.state == "FAILED"
    assert bal(tenant, "Bank:available") == 10_000
    assert_ledger_invariants(tenant)


def test_veto_after_cooloff_rejected(tenant: uuid.UUID) -> None:
    fund(tenant, 10_000)
    now = timezone.now()
    p = decide(proposed(tenant, 2_000), "GUARDED", now=now)
    with pytest.raises(ProblemError) as err:
        veto(p, now=now + timedelta(seconds=601))
    assert err.value.code == "COOLOFF_ELAPSED"
    assert deliver(p, now=now + timedelta(seconds=601)).state == "SUBMITTED"


def test_wrong_actor_rejected(tenant: uuid.UUID) -> None:
    p = extract(received(tenant), 1_000)
    with pytest.raises(ProblemError) as err:
        transition(p, "PROPOSED", "proposal_received", SYSTEM)
    assert err.value.code == "FORBIDDEN_SCOPE"


def test_wrong_trigger_rejected(tenant: uuid.UUID) -> None:
    p = received(tenant)
    with pytest.raises(IllegalTransitionError):
        transition(p, "EXTRACTED", "proposal_received", SYSTEM)


def test_every_transition_writes_exactly_one_event(tenant: uuid.UUID) -> None:
    fund(tenant, 10_000)
    p = settle(deliver(decide(proposed(tenant, 1_000), "ALLOW")))
    evs = events(tenant, p)
    assert len(evs) == 1 + len([e for e in evs if e.data["from"] is not None])
    assert {(e.data["from"], e.data["to"]) for e in evs if e.data["from"]} <= set(state_machine().transitions)
