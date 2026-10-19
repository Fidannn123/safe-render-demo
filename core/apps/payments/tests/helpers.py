"""Test drivers for the payment state machine (SYNTHETIC data only)."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from django.db import connection
from django.utils import timezone

from apps.audit.chain import verify_chain
from apps.ledger.models import Balance, Posting
from apps.ledger.service import balance_of, post_template
from apps.ledger.spec import ledger_spec
from apps.ledger.tenancy import tenant_transaction
from apps.payments.models import Payment
from apps.payments.service import create_payment, transition
from apps.payments.types import (
    SYSTEM,
    Actor,
    DecisionBinding,
    ExtractionBinding,
    ProposalBinding,
    TransitionContext,
)

USD = "USD"
VENDOR = uuid.UUID("0a0a0a0a-0000-4000-8000-000000000001")
REGISTRY_ACCOUNT = uuid.UUID("0b0b0b0b-0000-4000-8000-000000000001")
AGENT = Actor("agent", "agent-1")
GUARDIAN = Actor("guardian", "guardian")
BANK = Actor("bank", "bank-sandbox")
RECONCILER = Actor("reconciler", "recon")
APPROVER_1 = "a1a1a1a1-0000-4000-8000-000000000001"
APPROVER_2 = "a2a2a2a2-0000-4000-8000-000000000002"
OWNER = Actor("human", APPROVER_1)
ATTEST_APPROVAL = frozenset({"hard_rules_pass", "payee_account_unchanged"})


def fund(tenant: uuid.UUID, amount: int) -> None:
    with tenant_transaction(tenant):
        post_template(tenant, "opening_balance", amount, USD)


def received(tenant: uuid.UUID) -> Payment:
    return create_payment(tenant, uuid.uuid4())


def extract(p: Payment, amount: int, *, vendor: uuid.UUID | None = VENDOR, ref: str | None = None) -> Payment:
    ex = ExtractionBinding(
        vendor_id=vendor, amount_minor=amount, currency=USD, reference=ref or uuid.uuid4().hex
    )
    return transition(p, "EXTRACTED", "extraction_validated", SYSTEM, TransitionContext(extraction=ex))


def propose(p: Payment) -> Payment:
    assert p.amount_minor is not None
    pr = ProposalBinding(
        vendor_id=VENDOR, account_id=REGISTRY_ACCOUNT, amount_minor=p.amount_minor, currency=USD
    )
    return transition(p, "PROPOSED", "proposal_received", AGENT, TransitionContext(proposal=pr))


def proposed(tenant: uuid.UUID, amount: int, ref: str | None = None) -> Payment:
    return propose(extract(received(tenant), amount, ref=ref))


VERDICTS: dict[str, tuple[str, str, str | None]] = {
    "ALLOW": ("CLEARED", "decision_allow_or_guarded", "T1"),
    "GUARDED": ("CLEARED", "decision_allow_or_guarded", "T2"),
    "HOLD": ("HELD", "decision_hold", "T3"),
    "DENY": ("DENIED", "decision_deny", None),
}


def decide(
    p: Payment,
    verdict: str,
    *,
    now: datetime | None = None,
    approvals_required: int = 1,
    warnings: tuple[str, ...] = (),
) -> Payment:
    to, trigger, tier = VERDICTS[verdict]
    now = now or timezone.now()
    d = DecisionBinding(
        decision_id=uuid.uuid4(),
        tier=tier,
        cooloff_until=now + timedelta(seconds=600) if tier == "T2" else None,
        approvals_required=approvals_required if verdict == "HOLD" else 0,
        approval_warnings=warnings,
    )
    return transition(p, to, trigger, GUARDIAN, TransitionContext(decision=d, now=now))


def approve(
    p: Payment,
    approvers: tuple[str, ...] = (APPROVER_1,),
    *,
    now: datetime | None = None,
    **overrides: Any,
) -> Payment:
    now = now or timezone.now()
    fields: dict[str, Any] = {
        "now": now,
        "reason": "invoice verified by phone",
        "approver_ids": approvers,
        "step_up_at": now,
        "attested": ATTEST_APPROVAL,
        **overrides,
    }
    return transition(
        p, "APPROVED", "approvals_complete", Actor("human", approvers[-1]), TransitionContext(**fields)
    )


def reject(p: Payment, reject_reason: str, *, now: datetime | None = None) -> Payment:
    now = now or timezone.now()
    ctx = TransitionContext(
        now=now, reason="vendor says not ours", reject_reason=reject_reason, step_up_at=now
    )
    return transition(p, "REJECTED", "human_reject", OWNER, ctx)


def deliver(p: Payment, *, now: datetime | None = None) -> Payment:
    return transition(p, "SUBMITTED", "outbox_delivered", SYSTEM, TransitionContext(now=now))


def settle(p: Payment) -> Payment:
    ctx = TransitionContext(attested=frozenset({"receipt_signature_valid"}))
    return transition(p, "SETTLED", "bank_receipt_settled", BANK, ctx)


def bank_fail(p: Payment) -> Payment:
    ctx = TransitionContext(attested=frozenset({"receipt_signature_valid"}))
    return transition(p, "FAILED", "bank_receipt_failed", BANK, ctx)


def veto(p: Payment, *, now: datetime | None = None) -> Payment:
    now = now or timezone.now()
    ctx = TransitionContext(now=now, reason="owner veto", step_up_at=now, actor_role="owner")
    return transition(p, "FAILED", "t2_veto", OWNER, ctx)


def reconcile(p: Payment) -> Payment:
    return transition(p, "RECONCILED", "recon_matched", RECONCILER)


def bal(tenant: uuid.UUID, code: str) -> int:
    with tenant_transaction(tenant):
        return balance_of(tenant, code, USD)


def assert_ledger_invariants(tenant: uuid.UUID) -> None:
    """Every entry sums to zero per currency; bank accounts >= 0; balances == sum of postings; chain ok."""
    with tenant_transaction(tenant):
        with connection.cursor() as cur:
            cur.execute(
                "SELECT entry_id, currency, sum(amount_minor) FROM ledger_posting WHERE tenant_id = %s "
                "GROUP BY entry_id, currency HAVING sum(amount_minor) <> 0",
                [str(tenant)],
            )
            assert cur.fetchall() == []
            cur.execute(
                "SELECT count(*) FROM ledger_journal_entry e WHERE e.tenant_id = %s AND "
                "(SELECT count(*) FROM ledger_posting p WHERE p.entry_id = e.id) < 2",
                [str(tenant)],
            )
            assert cur.fetchone() == (0,)
        sums: dict[tuple[uuid.UUID, str], int] = {}
        for post in Posting.objects.filter(tenant_id=tenant).select_related("account"):
            k = (post.account_id, post.currency)
            sums[k] = sums.get(k, 0) + post.account.normal_sign * post.amount_minor
        balances = {(b.account_id, b.currency): b.balance for b in Balance.objects.filter(tenant_id=tenant)}
        assert balances == sums
        nonneg = set(ledger_spec().non_negative_accounts)
        for b in Balance.objects.filter(tenant_id=tenant).select_related("account"):
            if b.account.code in nonneg:
                assert b.balance >= 0, b.account.code
    with tenant_transaction(tenant):
        verify_chain(tenant)
