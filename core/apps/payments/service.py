"""Payment state machine executor (spec/state_machine.yaml; human review: M1).

transition() commits ONE database transaction containing, per step: the payment state row, the ledger
postings of the spec template (when its posting_condition holds), exactly one audit event, and the outbox
row when the spec says so. Steps of an atomic chain (PROPOSED->CLEARED->RESERVED, HELD->APPROVED->RESERVED)
run in the same transaction. Every (from, to) pair absent from the spec raises ILLEGAL_TRANSITION.
All payment transitions of a tenant are serialised by the tenant-limits row lock.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, time
from typing import Any

import psycopg.errors
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.audit.chain import append_event
from apps.ledger.service import post_template
from apps.ledger.tenancy import bind_tenant

from .conditions import compile_condition, compile_effect
from .errors import IllegalTransitionError, ProblemError
from .fingerprint import invoice_fingerprint
from .guards import GuardInput, get_guard, registered_guards
from .models import OutboxMessage, Payment, TenantLimits
from .spec import StateMachineSpec, TransitionSpec, state_machine
from .types import SYSTEM, Actor, DecisionBinding, TransitionContext

AUDIT_ACTION = "PAYMENT_TRANSITION"
CREATE_TRIGGER = "invoice_received"
LIVE_FINGERPRINT_CONSTRAINT = "payments_live_fingerprint_uniq"
NON_NEGATIVE_CONSTRAINT = "ledger_balance_non_negative_chk"


def validate_spec_bindings(sm: StateMachineSpec | None = None) -> None:
    """Startup check: every guard, posting_condition and effect named in the spec is implemented."""
    sm = sm or state_machine()
    known = registered_guards()
    for t in sm.transitions.values():
        missing = [g for g in t.guards if g not in known]
        if missing:
            raise ValueError(f"{t.from_state}->{t.to_state}: no implementation for guards {missing}")
        if t.posting_condition:
            compile_condition(t.posting_condition)
        for effect in t.effects:
            compile_effect(effect)


def lock_tenant_limits(tenant_id: uuid.UUID) -> TenantLimits:
    """SELECT ... FOR UPDATE on the tenant's limits row (created on first use). Inside a transaction."""
    bind_tenant(tenant_id)
    TenantLimits.objects.bulk_create([TenantLimits(tenant_id=tenant_id)], ignore_conflicts=True)
    return TenantLimits.objects.select_for_update().get(tenant_id=tenant_id)


def _audit(
    payment: Payment,
    actor: Actor,
    *,
    from_state: str | None,
    to_state: str,
    trigger: str,
    data: dict[str, Any],
    now: datetime,
) -> int:
    ev = append_event(
        tenant_id=payment.tenant_id,
        actor=actor.as_dict(),
        action=AUDIT_ACTION,
        subject_type="payment",
        subject_id=str(payment.id),
        data={"from": from_state, "to": to_state, "trigger": trigger, **data},
        occurred_at=now,
    )
    return ev.seq


def create_payment(
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: Actor = SYSTEM,
    *,
    payment_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> Payment:
    """Create a payment in the spec's initial state (one audit event, from = null)."""
    now = now or timezone.now()
    sm = state_machine()
    with transaction.atomic():
        lock_tenant_limits(tenant_id)
        p = Payment.objects.create(
            id=payment_id or uuid.uuid4(), tenant_id=tenant_id, invoice_id=invoice_id, state=sm.initial
        )
        _audit(
            p,
            actor,
            from_state=None,
            to_state=sm.initial,
            trigger=CREATE_TRIGGER,
            data={"journal_entry_id": None, "outbox_id": None},
            now=now,
        )
    return p


def _apply_decision(p: Payment, d: DecisionBinding) -> None:
    p.decision_id = d.decision_id
    p.tier = d.tier
    p.cooloff_until = d.cooloff_until
    p.approvals_required = d.approvals_required
    p.approval_warnings = list(d.approval_warnings)


def _check_same(p: Payment, field: str, value: object) -> None:
    current = getattr(p, field)
    if current is not None and current != value:
        raise ProblemError("VALIDATION_FAILED", f"proposal {field} differs from the bound invoice")


def _refresh_fingerprint(p: Payment) -> None:
    if p.vendor_id is not None and p.amount_minor is not None and p.currency is not None:
        p.invoice_fingerprint = invoice_fingerprint(p.vendor_id, p.reference, p.amount_minor, p.currency)


def _bind(p: Payment, t: TransitionSpec, ctx: TransitionContext, actor: Actor) -> None:
    """Copy caller-supplied, already-validated data onto the row for the triggers that carry data."""
    if t.trigger == "extraction_validated":
        ex = ctx.extraction
        if ex is None:
            raise ProblemError("VALIDATION_FAILED", "extraction binding required")
        p.vendor_id, p.amount_minor, p.currency = ex.vendor_id, ex.amount_minor, ex.currency
        p.reference, p.due_date, p.expense_account = ex.reference, ex.due_date, ex.expense_account
        _refresh_fingerprint(p)
    elif t.trigger == "proposal_received":
        pr = ctx.proposal
        if pr is not None:
            if p.invoice_accrued:  # the accrual binds vendor and amount; a proposal cannot change them
                for field in ("vendor_id", "amount_minor", "currency"):
                    _check_same(p, field, getattr(pr, field))
            p.vendor_id, p.account_id = pr.vendor_id, pr.account_id
            p.amount_minor, p.currency = pr.amount_minor, pr.currency
            _refresh_fingerprint(p)
        p.initiated_by = actor.as_dict()
        p.reject_reason = None
        p.decision_id, p.tier, p.cooloff_until = None, None, None
        p.approvals_required, p.approval_warnings = 0, []
    elif t.trigger.startswith("decision_"):
        if ctx.decision is None:
            raise ProblemError("VALIDATION_FAILED", "decision binding required")
        _apply_decision(p, ctx.decision)
    elif t.trigger == "human_reject":
        p.reject_reason = ctx.reject_reason


def _post(p: Payment, t: TransitionSpec) -> uuid.UUID | None:
    if t.posting is None:
        return None
    if t.posting_condition and not compile_condition(t.posting_condition)(p):
        return None
    if p.amount_minor is None or p.currency is None:
        raise ProblemError("VALIDATION_FAILED", f"posting {t.posting} needs amount and currency")
    reverses = None
    if t.posting == "invoice_voided" and p.accrual_entry_id is not None:
        from apps.ledger.models import JournalEntry  # noqa: PLC0415

        reverses = JournalEntry.objects.get(id=p.accrual_entry_id)
    entry = post_template(
        p.tenant_id,
        t.posting,
        p.amount_minor,
        p.currency,
        vendor_id=p.vendor_id,
        expense_account=p.expense_account,
        payment_id=p.id,
        reverses=reverses,
        memo=f"{t.from_state}->{t.to_state}",
    )
    if t.posting == "invoice_accepted":
        p.invoice_accrued, p.accrual_entry_id = True, entry.id
    elif t.posting == "invoice_voided":
        p.invoice_accrued = False
    return entry.id


def _outbox(p: Payment, t: TransitionSpec, now: datetime) -> uuid.UUID | None:
    if t.outbox is None:
        return None
    available = p.cooloff_until if p.tier == "T2" and p.cooloff_until and p.cooloff_until > now else now
    if p.requested_execution_date:
        available = max(available, datetime.combine(p.requested_execution_date, time.min, tzinfo=UTC))
    msg = OutboxMessage.objects.create(
        tenant_id=p.tenant_id,
        payment_id=p.id,
        kind=t.outbox,
        payload={"payment_id": str(p.id), "end_to_end_id": str(p.id)},
        available_at=available,
    )
    return msg.id


def _audit_extras(t: TransitionSpec, ctx: TransitionContext) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    if ctx.reason is not None and "reason_present" in t.guards:
        extras["reason"] = ctx.reason
    if t.trigger == "human_reject":
        extras["reject_reason"] = ctx.reject_reason
    if "approvals_count_met" in t.guards:
        extras["approver_ids"] = sorted(ctx.approver_ids)
        extras["acknowledged_warnings"] = sorted(ctx.acknowledged_warnings)
    if ctx.decision is not None and t.trigger.startswith("decision_"):
        extras["decision_id"] = str(ctx.decision.decision_id)
    return extras


def _step(
    p: Payment,
    t: TransitionSpec,
    actor: Actor,
    *,
    ctx: TransitionContext,
    limits: TenantLimits,
    now: datetime,
) -> None:
    if actor.type != t.actor:
        raise ProblemError("FORBIDDEN_SCOPE", f"{t.from_state}->{t.to_state} requires actor {t.actor}")
    for name in t.guards:
        get_guard(name)(GuardInput(payment=p, transition=t, ctx=ctx, limits=limits, now=now))
    _bind(p, t, ctx, actor)
    p.state = t.to_state
    for effect in t.effects:
        compile_effect(effect)(p)
    journal_entry_id = _post(p, t)
    p.save()
    outbox_id = _outbox(p, t, now)
    data: dict[str, Any] = {
        **{k: v for k, v in ctx.audit_data.items() if k not in {"from", "to", "trigger"}},
        **_audit_extras(t, ctx),
        "journal_entry_id": str(journal_entry_id) if journal_entry_id else None,
        "outbox_id": str(outbox_id) if outbox_id else None,
    }
    _audit(p, actor, from_state=t.from_state, to_state=t.to_state, trigger=t.trigger, data=data, now=now)


def _lookup(sm: StateMachineSpec, p: Payment, to_state: str) -> TransitionSpec:
    if sm.is_terminal(p.state, {"reject_reason": p.reject_reason}):
        raise IllegalTransitionError(f"{p.state} is terminal")
    t = sm.get(p.state, to_state)
    if t is None:
        raise IllegalTransitionError(f"{p.state}->{to_state} is not in the state machine")
    return t


def transition(
    payment: Payment,
    to_state: str,
    trigger: str,
    actor: Actor,
    context: TransitionContext | None = None,
) -> Payment:
    """Apply one spec transition (plus its atomic-chain continuation). Returns the updated row."""
    sm = state_machine()
    ctx = context or TransitionContext()
    now = ctx.now or timezone.now()
    with transaction.atomic():
        limits = lock_tenant_limits(payment.tenant_id)
        p = Payment.objects.select_for_update().get(pk=payment.pk, tenant_id=payment.tenant_id)
        first = _lookup(sm, p, to_state)
        if first.trigger != trigger:
            raise IllegalTransitionError(
                f"{p.state}->{to_state} is triggered by {first.trigger}, not {trigger}"
            )
        steps: list[tuple[TransitionSpec, Actor]] = [(first, actor)]
        prev = to_state
        for nxt in sm.chain_continuation(p.state, to_state):
            t = sm.get(prev, nxt)
            if t is None:  # pragma: no cover - parse_state_machine validates chains
                raise IllegalTransitionError(f"atomic chain {prev}->{nxt} missing")
            steps.append((t, SYSTEM if t.actor == "system" else actor))
            prev = nxt
        try:
            with transaction.atomic():
                for t, step_actor in steps:
                    _step(p, t, step_actor, ctx=ctx, limits=limits, now=now)
        except IntegrityError as exc:
            raise _translate_integrity(exc) from exc
    return p


def _translate_integrity(exc: IntegrityError) -> ProblemError:
    cause = exc.__cause__
    name = getattr(getattr(cause, "diag", None), "constraint_name", None)
    if isinstance(cause, psycopg.errors.CheckViolation) and name == NON_NEGATIVE_CONSTRAINT:
        return ProblemError("FUNDS_UNAVAILABLE", "reservation would overdraw a bank account")
    if isinstance(cause, psycopg.errors.UniqueViolation) and name == LIVE_FINGERPRINT_CONSTRAINT:
        return ProblemError("DUPLICATE_PAYMENT", "a live payment for this invoice already exists")
    return ProblemError("INTERNAL", "database integrity error")
