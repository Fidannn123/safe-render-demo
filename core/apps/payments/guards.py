"""Guard registry for spec/state_machine.yaml `guards:` names.

Every guard named in the spec must have a registered callable (checked at startup). A guard returns
None when it passes and raises ProblemError (typed spec/errors.yaml code) when it fails.

Structural guards are implemented here. Guards that need another module's evidence (hard_rules_pass,
payee_account_unchanged -> M2 recheck_for_approval; receipt_signature_valid -> M9) fail closed unless
the caller lists the guard name in TransitionContext.attested after running that check, or the owning
module replaces the implementation with register_guard(name, fn, replace=True).
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import yaml
from django.conf import settings

from apps.ledger.service import balance_of

from .errors import ProblemError
from .models import REJECT_REASONS, Payment, TenantLimits
from .spec import TransitionSpec
from .types import TransitionContext

MIN_REASON_LENGTH = 3  # ApproveBody / RejectBody reason minLength


@dataclass(frozen=True, slots=True)
class GuardInput:
    payment: Payment
    transition: TransitionSpec
    ctx: TransitionContext
    limits: TenantLimits
    now: datetime


GuardFn = Callable[[GuardInput], None]
_REGISTRY: dict[str, GuardFn] = {}


def register_guard(name: str, fn: GuardFn, *, replace: bool = False) -> None:
    if name in _REGISTRY and not replace:
        raise ValueError(f"guard {name!r} already registered (pass replace=True to override)")
    _REGISTRY[name] = fn


def get_guard(name: str) -> GuardFn:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise ProblemError("INTERNAL", f"guard {name!r} has no implementation") from None


def registered_guards() -> frozenset[str]:
    return frozenset(_REGISTRY)


def guard(name: str) -> Callable[[GuardFn], GuardFn]:
    def deco(fn: GuardFn) -> GuardFn:
        register_guard(name, fn)
        return fn

    return deco


@functools.cache
def step_up_max_age() -> timedelta:
    path = Path(settings.SAFEPAY_SPEC_DIR) / "policy.yaml"
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    return timedelta(seconds=int(raw["timing"]["step_up_otp_max_age_seconds"]))


def _attested(g: GuardInput, name: str, code: str, detail: str) -> None:
    if name not in g.ctx.attested:
        raise ProblemError(code, detail)


@guard("step_up_fresh")
def step_up_fresh(g: GuardInput) -> None:
    at = g.ctx.step_up_at
    if at is None or at > g.now or g.now - at > step_up_max_age():
        raise ProblemError("STEP_UP_REQUIRED", "fresh one-time password required")


@guard("approver_not_initiator")
def approver_not_initiator(g: GuardInput) -> None:
    initiator = (g.payment.initiated_by or {}).get("id")
    if initiator is not None and str(initiator) in g.ctx.approver_ids:
        raise ProblemError("SELF_APPROVAL", "the initiator cannot approve this payment")


@guard("distinct_approvers")
def distinct_approvers(g: GuardInput) -> None:
    if len(set(g.ctx.approver_ids)) != len(g.ctx.approver_ids):
        raise ProblemError("ALREADY_APPROVED_BY_USER", "approvers must be distinct")


@guard("reason_present")
def reason_present(g: GuardInput) -> None:
    if g.ctx.reason is None or len(g.ctx.reason.strip()) < MIN_REASON_LENGTH:
        raise ProblemError("REASON_REQUIRED", "a reason of at least 3 characters is required")


@guard("approvals_count_met")
def approvals_count_met(g: GuardInput) -> None:
    required = max(1, g.payment.approvals_required)
    if len(set(g.ctx.approver_ids)) < required:
        raise ProblemError(
            "ILLEGAL_TRANSITION", f"{required} distinct approvals required; payment stays HELD"
        )


@guard("hard_rules_pass")
def hard_rules_pass(g: GuardInput) -> None:
    _attested(g, "hard_rules_pass", "HARD_RULE_FAILED", "hard rules not re-checked at approval")


@guard("tenant_not_frozen")
def tenant_not_frozen(g: GuardInput) -> None:
    if g.limits.frozen:
        raise ProblemError("TENANT_FROZEN", "tenant is frozen")


@guard("payee_account_unchanged")
def payee_account_unchanged(g: GuardInput) -> None:
    _attested(
        g, "payee_account_unchanged", "PAYEE_CHANGED", "payee account not re-checked against the Decision"
    )


@guard("funds_available")
def funds_available(g: GuardInput) -> None:
    p = g.payment
    if p.amount_minor is None or p.currency is None:
        raise ProblemError("FUNDS_UNAVAILABLE", "payment has no amount")
    if balance_of(p.tenant_id, "Bank:available", p.currency) < p.amount_minor:
        raise ProblemError("FUNDS_UNAVAILABLE", "available funds do not cover the reservation")


@guard("warnings_acknowledged")
def warnings_acknowledged(g: GuardInput) -> None:
    missing = set(g.payment.approval_warnings or []) - set(g.ctx.acknowledged_warnings)
    if missing:
        raise ProblemError("WARNING_NOT_ACKNOWLEDGED", f"unacknowledged: {sorted(missing)}")


@guard("reject_reason_valid")
def reject_reason_valid(g: GuardInput) -> None:
    if g.ctx.reject_reason not in REJECT_REASONS:
        raise ProblemError("VALIDATION_FAILED", f"reject_reason must be one of {list(REJECT_REASONS)}")


@guard("reject_reason_not_now")
def reject_reason_not_now(g: GuardInput) -> None:
    if g.payment.reject_reason != "not_now":
        raise ProblemError("ILLEGAL_TRANSITION", "only a payment rejected with not_now may be proposed again")


@guard("cooloff_elapsed_if_t2")
def cooloff_elapsed_if_t2(g: GuardInput) -> None:
    p = g.payment
    if p.tier == "T2" and (p.cooloff_until is None or g.now < p.cooloff_until):
        raise ProblemError("ILLEGAL_TRANSITION", "T2 cool-off has not elapsed")


@guard("receipt_signature_valid")
def receipt_signature_valid(g: GuardInput) -> None:
    _attested(g, "receipt_signature_valid", "VALIDATION_FAILED", "bank receipt signature not verified")


@guard("owner_role")
def owner_role(g: GuardInput) -> None:
    if g.ctx.actor_role != "owner":
        raise ProblemError("FORBIDDEN_SCOPE", "owner role required")


@guard("cooloff_not_elapsed")
def cooloff_not_elapsed(g: GuardInput) -> None:
    p = g.payment
    if p.tier != "T2" or p.cooloff_until is None or g.now >= p.cooloff_until:
        raise ProblemError("COOLOFF_ELAPSED", "T2 cool-off has elapsed; veto no longer possible")
