"""Approval-time re-check of a HELD payment (DECISIONS.md D-029, policy.approvals).

The caller (approvals, Phase 2) runs this inside ONE transaction under the tenant-limits row lock when
the final required approval arrives, passing the Decision's stored canonical inputs and the current
state of the world. Checks, in policy order:
  hard_rules              every reason_codes.hard rule except TENANT_FROZEN -> HARD_RULE_FAILED
  tenant_not_frozen       -> TENANT_FROZEN
  payee_account_unchanged registry account vs the inputs bound by inputs_sha256 -> PAYEE_CHANGED
  funds_available         Bank:available >= amount -> FUNDS_UNAVAILABLE
Warnings (policy.approvals.warnings_requiring_ack, e.g. CASH_BUFFER_BREACH) must be acknowledged,
else WARNING_NOT_ACKNOWLEDGED. Agent caps are NEVER re-applied (reapply_agent_caps: false).
Pure: no database access; the caller supplies every current value.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from .canonical import sha256_hex
from .inputs import ForecastBuffer, GuardianInputs, HistoryPayment, RegistryAccount, VendorSnapshot
from .policy import Policy
from .rules import RULES, RuleContext


class RecheckFailure(StrEnum):
    """Typed failures; values are spec/errors.yaml codes."""

    HARD_RULE_FAILED = "HARD_RULE_FAILED"
    TENANT_FROZEN = "TENANT_FROZEN"
    PAYEE_CHANGED = "PAYEE_CHANGED"
    FUNDS_UNAVAILABLE = "FUNDS_UNAVAILABLE"
    WARNING_NOT_ACKNOWLEDGED = "WARNING_NOT_ACKNOWLEDGED"


class InputsIntegrityError(Exception):
    """Stored canonical inputs do not hash to the Decision's inputs_sha256 (tampering or corruption)."""


@dataclass(frozen=True, slots=True)
class ApprovalContext:
    """Current values read by the caller under the tenant-limits row lock."""

    vendor: VendorSnapshot
    registry_account: RegistryAccount | None
    history: tuple[HistoryPayment, ...]
    tenant_frozen: bool
    bank_available_minor: int
    now: dt.datetime
    forecast: ForecastBuffer | None = None  # current forecast; None -> the one bound in the Decision
    acknowledged_warnings: Collection[str] = ()


@dataclass(frozen=True, slots=True)
class RecheckResult:
    failures: tuple[RecheckFailure, ...]
    failed_hard_rules: tuple[str, ...]
    warnings: tuple[str, ...]
    unacknowledged_warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def error_code(self) -> RecheckFailure | None:
        """The problem code to return (first failure in policy order), None when approval may proceed."""
        return self.failures[0] if self.failures else None


def load_bound_inputs(canonical_inputs: bytes, inputs_sha256: str) -> GuardianInputs:
    if sha256_hex(canonical_inputs) != inputs_sha256:
        raise InputsIntegrityError("stored inputs do not match inputs_sha256")
    return GuardianInputs.model_validate_json(canonical_inputs)


def recheck_for_approval(
    canonical_inputs: bytes, inputs_sha256: str, current: ApprovalContext, policy: Policy
) -> RecheckResult:
    bound = load_bound_inputs(canonical_inputs, inputs_sha256)
    fresh = bound.model_copy(
        update={
            "vendor": current.vendor,
            "registry_account": current.registry_account,
            "history": tuple(sorted(current.history, key=lambda h: (h.created_at, str(h.payment_id)))),
            "tenant_frozen": current.tenant_frozen,
            "now": current.now.astimezone(dt.UTC),
            "forecast": current.forecast or bound.forecast,
        }
    )
    ctx = RuleContext.build(fresh, policy)
    failed_hard = tuple(
        code for code in policy.hard_codes if code != "TENANT_FROZEN" and RULES[code](ctx) is not None
    )
    failures: list[RecheckFailure] = []
    if failed_hard:
        failures.append(RecheckFailure.HARD_RULE_FAILED)
    if current.tenant_frozen:
        failures.append(RecheckFailure.TENANT_FROZEN)
    if _payee_changed(bound.registry_account, current.registry_account):
        failures.append(RecheckFailure.PAYEE_CHANGED)
    if current.bank_available_minor < bound.proposal.amount_minor:
        failures.append(RecheckFailure.FUNDS_UNAVAILABLE)
    warnings = tuple(w for w in policy.warnings_requiring_ack if RULES[w](ctx) is not None)
    acknowledged = set(current.acknowledged_warnings)
    unacknowledged = tuple(w for w in warnings if w not in acknowledged)
    if unacknowledged:
        failures.append(RecheckFailure.WARNING_NOT_ACKNOWLEDGED)
    return RecheckResult(
        failures=tuple(failures),
        failed_hard_rules=failed_hard,
        warnings=warnings,
        unacknowledged_warnings=unacknowledged,
    )


def _payee_changed(bound: RegistryAccount | None, current: RegistryAccount | None) -> bool:
    if bound is None or current is None:
        return bound is not current
    return bound.identity() != current.identity()
