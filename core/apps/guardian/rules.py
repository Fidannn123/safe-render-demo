"""One function per reason code in spec/policy.yaml (hard, t3, signals). Pure: no I/O, no clock.

Each rule receives a RuleContext and returns a deterministic, template-generated detail string when it
fires, else None. Details never quote untrusted text (only field names and policy numbers).
RULES maps every policy reason code to its function; tests assert the key set equals the policy's.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from .detectors import (
    accounts_look_alike,
    damerau_levenshtein,
    injection_marker,
    names_look_alike,
    normalise_reference,
    robust_z_exceeds,
)
from .inputs import GuardianInputs, HistoryPayment, RegistryAccount
from .policy import Policy

# Money committed or gone (policy.caps.counted_states); used for amount history.
COUNTED_STATES = frozenset({"RESERVED", "SUBMITTED", "SETTLED", "RECONCILED"})
# Already paid or being paid: CLEARED and APPROVED are transient states inside atomic chains to RESERVED.
PAID_STATES = COUNTED_STATES | {"CLEARED", "APPROVED"}
# Payments that did not / will not move money; ignored by duplicate and split detection.
DEAD_STATES = frozenset({"DENIED", "FAILED", "REJECTED"})


def payee_new_or_changed(account: RegistryAccount | None, now: dt.datetime, window_days: int) -> bool:
    """Registry account first used or changed < window_days ago (never used counts as new)."""
    if account is None or account.first_used_at is None:
        return True
    window = dt.timedelta(days=window_days)
    return now - account.first_used_at < window or now - account.changed_at < window


@dataclass(frozen=True, slots=True)
class RuleContext:
    inputs: GuardianInputs
    policy: Policy
    new_or_changed: bool

    @classmethod
    def build(cls, inputs: GuardianInputs, policy: Policy) -> RuleContext:
        new = payee_new_or_changed(inputs.registry_account, inputs.now, policy.new_or_changed_window_days)
        return cls(inputs=inputs, policy=policy, new_or_changed=new)

    def other_history(self) -> list[HistoryPayment]:
        pid = self.inputs.proposal.payment_id
        return [h for h in self.inputs.history if h.payment_id != pid and h.created_at <= self.inputs.now]


Rule = Callable[[RuleContext], str | None]


# ------------------------------------------------------------------------------------------- hard
def payee_not_in_registry(ctx: RuleContext) -> str | None:
    p, acct, vendor = ctx.inputs.proposal, ctx.inputs.registry_account, ctx.inputs.vendor
    if acct is None:
        return "Vendor has no verified registry bank account."
    if vendor.vendor_id != p.vendor_id or acct.vendor_id != p.vendor_id:
        return "Registry account does not belong to the proposed vendor."
    if acct.account_id != p.account_id:
        return "Proposed account is not the vendor's verified registry account."
    return None


def currency_not_allowed(ctx: RuleContext) -> str | None:
    if ctx.inputs.proposal.currency in ctx.policy.allowed_currencies:
        return None
    allowed = ", ".join(sorted(ctx.policy.allowed_currencies))
    return f"Currency is not in the allowed list ({allowed})."


def duplicate_paid(ctx: RuleContext) -> str | None:
    fp = ctx.inputs.proposal.invoice_fingerprint
    if any(h.invoice_fingerprint == fp and h.state in PAID_STATES for h in ctx.other_history()):
        return "An identical invoice (same fingerprint) is already paid or being paid."
    return None


def vendor_blocked(ctx: RuleContext) -> str | None:
    return "Vendor is blocked in the registry." if ctx.inputs.vendor.blocked else None


def tenant_frozen(ctx: RuleContext) -> str | None:
    return "Tenant is frozen (T4); no outflow is allowed." if ctx.inputs.tenant_frozen else None


def amount_invalid(ctx: RuleContext) -> str | None:
    p, ex = ctx.inputs.proposal, ctx.inputs.extraction
    if p.amount_minor <= 0:
        return "Amount must be a positive integer in minor units."
    if (p.amount_minor, p.currency) != (ex.amount_minor, ex.currency):
        return "Proposed amount or currency differs from the extracted invoice."
    return None


# ------------------------------------------------------------------------------------------- t3
def over_per_tx_cap(ctx: RuleContext) -> str | None:
    cap = ctx.policy.per_tx_cap
    if ctx.inputs.proposal.amount_minor > cap:
        return f"Amount exceeds the per-transaction cap of {cap} minor units."
    return None


def over_rolling_24h_cap(ctx: RuleContext) -> str | None:
    cap = ctx.policy.rolling_24h_cap
    if ctx.inputs.rolling.outflow_24h_minor + ctx.inputs.proposal.amount_minor > cap:
        return f"Rolling 24 h outflow would exceed {cap} minor units."
    return None


def over_new_payee_cap(ctx: RuleContext) -> str | None:
    cap = ctx.policy.new_payee_cap
    if ctx.new_or_changed and ctx.inputs.proposal.amount_minor > cap:
        return f"Amount to a new or changed payee exceeds {cap} minor units."
    return None


def over_new_payee_rolling_24h_cap(ctx: RuleContext) -> str | None:
    cap = ctx.policy.new_payee_rolling_24h_cap
    total = ctx.inputs.rolling.new_payee_outflow_24h_minor + ctx.inputs.proposal.amount_minor
    if ctx.new_or_changed and total > cap:
        return f"Rolling 24 h outflow to new or changed payees would exceed {cap} minor units."
    return None


def cash_buffer_breach(ctx: RuleContext) -> str | None:
    f = ctx.inputs.forecast
    if f.breach:
        return (
            f"Forecast minimum balance {f.projected_min_balance_minor} falls below buffer {f.buffer_minor}."
        )
    return None


def network_unavailable(ctx: RuleContext) -> str | None:
    # Fail closed for new/changed payees only; a missing check (SKIP) for a new payee also fails closed.
    if ctx.new_or_changed and ctx.inputs.network.status in ("UNAVAILABLE", "SKIP"):
        return "Fraud network unavailable for a new or changed payee (fail closed)."
    return None


# ------------------------------------------------------------------------------------------- signals
def bank_detail_mismatch(ctx: RuleContext) -> str | None:
    shown, acct = ctx.inputs.extraction.bank_details_on_invoice, ctx.inputs.registry_account
    if shown is None or acct is None:
        return None
    if (shown.country, shown.iban) != (acct.country, acct.iban):
        return "Bank details on the invoice differ from the registry account."
    return None


def lookalike_vendor(ctx: RuleContext) -> str | None:
    if not ctx.new_or_changed:
        return None
    me = ctx.inputs.vendor
    limit = ctx.policy.lookalike_max_damerau_levenshtein
    for other in ctx.inputs.known_vendors:
        if other.vendor_id != me.vendor_id and names_look_alike(me.name, other.name, limit):
            return (
                f"Vendor name looks like registry vendor {other.vendor_id} "
                f"(TR39 skeleton, distance <= {limit})."
            )
    return None


def lookalike_account(ctx: RuleContext) -> str | None:
    acct = ctx.inputs.registry_account
    if not ctx.new_or_changed or acct is None:
        return None
    chars = ctx.policy.lookalike_account_prefix_suffix_chars
    for other in ctx.inputs.known_vendors:
        if other.vendor_id == acct.vendor_id:
            continue
        for known in other.accounts:
            if accounts_look_alike(acct.iban, known.iban, chars):
                return (
                    f"Payee account shares the first or last {chars} BBAN characters "
                    f"with vendor {other.vendor_id}."
                )
    return None


def network_reported_2plus(ctx: RuleContext) -> str | None:
    net = ctx.inputs.network
    if net.status == "HIT" and net.reporters >= 2:
        return f"Payee reported by {net.reporters} distinct network members."
    return None


def network_reported_1(ctx: RuleContext) -> str | None:
    net = ctx.inputs.network
    if net.status == "HIT" and net.reporters == 1:
        return "Payee reported by 1 network member."
    return None


def injection_markers(ctx: RuleContext) -> str | None:
    fields = sorted({t.field for t in ctx.inputs.extraction.untrusted_text if injection_marker(t.value)})
    if fields:
        return f"Instruction-like text in untrusted field(s): {', '.join(fields)[:400]}."
    return None


def duplicate_suspect(ctx: RuleContext) -> str | None:
    p, now = ctx.inputs.proposal, ctx.inputs.now
    window = dt.timedelta(days=ctx.policy.duplicate_window_days)
    limit = ctx.policy.duplicate_reference_max_edit_distance
    ref = normalise_reference(p.reference)
    for h in ctx.other_history():
        if (
            h.state not in DEAD_STATES
            and h.vendor_id == p.vendor_id
            and (h.amount_minor, h.currency) == (p.amount_minor, p.currency)
            and h.invoice_fingerprint != p.invoice_fingerprint
            and now - h.created_at <= window
            and damerau_levenshtein(ref, normalise_reference(h.reference)) <= limit
        ):
            return (
                f"Same vendor and amount within {ctx.policy.duplicate_window_days} days, similar reference."
            )
    return None


def split_pattern(ctx: RuleContext) -> str | None:
    p, now = ctx.inputs.proposal, ctx.inputs.now
    window = dt.timedelta(hours=ctx.policy.split_window_hours)
    prior = [
        h
        for h in ctx.other_history()
        if h.state not in DEAD_STATES and h.account_id == p.account_id and now - h.created_at < window
    ]
    count = len(prior) + 1
    total = sum(h.amount_minor for h in prior) + p.amount_minor
    # A new or changed payee is split under the new-payee cap, so its thresholds are tighter (D-045).
    min_count, cap = (
        (ctx.policy.split_new_payee_min_count, ctx.policy.split_new_payee_sum_exceeds)
        if ctx.new_or_changed
        else (ctx.policy.split_min_count, ctx.policy.split_sum_exceeds)
    )
    if count >= min_count and total > cap:
        return (
            f"{count} payments to this payee within {ctx.policy.split_window_hours} h sum to {total}, "
            f"over {cap} minor units."
        )
    return None


def amount_anomaly(ctx: RuleContext) -> str | None:
    p = ctx.inputs.proposal
    amounts = [
        h.amount_minor
        for h in ctx.other_history()
        if h.vendor_id == p.vendor_id and h.state in COUNTED_STATES and h.currency == p.currency
    ]
    if len(amounts) < ctx.policy.anomaly_min_history:  # D-015: too little history, not evaluated
        return None
    threshold = ctx.policy.anomaly_robust_z_threshold
    if robust_z_exceeds(p.amount_minor, amounts, threshold):
        return f"Amount is an outlier vs {len(amounts)} prior payments (robust z > {threshold})."
    return None


def new_or_changed_payee(ctx: RuleContext) -> str | None:
    if ctx.new_or_changed:
        days = ctx.policy.new_or_changed_window_days
        return f"Payee account first used or changed less than {days} days ago."
    return None


RULES: dict[str, Rule] = {
    "PAYEE_NOT_IN_REGISTRY": payee_not_in_registry,
    "CURRENCY_NOT_ALLOWED": currency_not_allowed,
    "DUPLICATE_PAID": duplicate_paid,
    "VENDOR_BLOCKED": vendor_blocked,
    "TENANT_FROZEN": tenant_frozen,
    "AMOUNT_INVALID": amount_invalid,
    "OVER_PER_TX_CAP": over_per_tx_cap,
    "OVER_ROLLING_24H_CAP": over_rolling_24h_cap,
    "OVER_NEW_PAYEE_CAP": over_new_payee_cap,
    "OVER_NEW_PAYEE_ROLLING_24H_CAP": over_new_payee_rolling_24h_cap,
    "CASH_BUFFER_BREACH": cash_buffer_breach,
    "NETWORK_UNAVAILABLE": network_unavailable,
    "BANK_DETAIL_MISMATCH": bank_detail_mismatch,
    "LOOKALIKE_VENDOR": lookalike_vendor,
    "LOOKALIKE_ACCOUNT": lookalike_account,
    "NETWORK_REPORTED_2PLUS": network_reported_2plus,
    "NETWORK_REPORTED_1": network_reported_1,
    "INJECTION_MARKERS": injection_markers,
    "DUPLICATE_SUSPECT": duplicate_suspect,
    "SPLIT_PATTERN": split_pattern,
    "AMOUNT_ANOMALY": amount_anomaly,
    "NEW_OR_CHANGED_PAYEE": new_or_changed_payee,
}
