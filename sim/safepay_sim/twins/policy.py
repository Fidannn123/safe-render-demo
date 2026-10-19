"""Reference reading of spec/policy.yaml used to label expected verdicts (sim-side oracle, not core).

Interpretations made here (the labels are only as good as these; core/apps/guardian is checked
against them by the harness):
  * new/changed payee: registry account changed, vendor added, or account first used < 30 days ago.
  * rolling sums count earlier scenario payments of the same twin whose verdict was ALLOW or GUARDED
    (reserved immediately); the window is (t - 24 h, t] and includes the current amount.
  * SPLIT_PATTERN: payments to the same payee in the window, including the current one, number >= 3
    and sum > per_tx_cap; for a new/changed payee number >= 2 and sum > new_payee_cap (D-045).
  * DUPLICATE_SUSPECT: an earlier invoice of the same vendor issued <= 30 days before with normalised
    reference (upper-case alphanumerics) at Damerau-Levenshtein distance <= 2.
  * DUPLICATE_PAID: same vendor, normalised reference, amount and currency as a settled payment.
  * AMOUNT_ANOMALY: >= 5 settled payments to the vendor; |x - median| / (1.4826 * MAD) > 3.5.
  * LOOKALIKE_ACCOUNT: payee BBAN shares its first or last 4 characters with another registry account.
  * CASH_BUFFER_BREACH: forecast minimum closing balance minus earlier executed scenario payments minus
    this amount < buffer.
  * Network (B2 only, new/changed payees only): reporters >= 2 -> NETWORK_REPORTED_2PLUS, 1 -> _1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

import yaml

from .iban import bban
from .model import Twin, Vendor
from .textutil import damerau_levenshtein, names_lookalike, normalise_ref

SPEC_DIR = Path(__file__).resolve().parents[3] / "spec"
MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class Policy:
    version: str
    per_tx_cap: int
    rolling_24h_cap: int
    new_payee_cap: int
    new_payee_rolling_24h_cap: int
    new_window_days: int
    score_max: int
    score_hold_threshold: int
    score_t1_max: int
    buffer_floor: int
    median_opex_weeks: int
    hurdle_apr: Fraction
    apr_day_count: int
    weights: dict[str, int]
    hard: tuple[str, ...]
    t3: tuple[str, ...]
    lookalike_distance: int
    account_chars: int
    dup_window_days: int
    dup_distance: int
    split_min_count: int
    split_new_payee_min_count: int
    z_threshold: Fraction
    z_min_history: int


def load_policy(path: Path | None = None) -> Policy:
    raw: dict[str, Any] = yaml.safe_load((path or SPEC_DIR / "policy.yaml").read_text(encoding="utf-8"))
    caps, det = raw["caps"], raw["detectors"]
    return Policy(
        version=str(raw["policy_version"]),
        per_tx_cap=int(caps["per_tx_cap"]),
        rolling_24h_cap=int(caps["rolling_24h_cap"]),
        new_payee_cap=int(caps["new_payee_cap"]),
        new_payee_rolling_24h_cap=int(caps["new_payee_rolling_24h_cap"]),
        new_window_days=int(raw["payee"]["new_or_changed_window_days"]),
        score_max=int(raw["scoring"]["score_max"]),
        score_hold_threshold=int(raw["scoring"]["score_hold_threshold"]),
        score_t1_max=int(raw["scoring"]["score_t1_max"]),
        buffer_floor=int(raw["buffer"]["buffer_floor"]),
        median_opex_weeks=int(raw["buffer"]["median_opex_weeks"]),
        hurdle_apr=Fraction(Decimal(raw["scheduling"]["early_pay_hurdle_apr"])),
        apr_day_count=int(raw["scheduling"]["apr_day_count"]),
        weights={str(k): int(v) for k, v in raw["reason_codes"]["signals"].items()},
        hard=tuple(raw["reason_codes"]["hard"]),
        t3=tuple(raw["reason_codes"]["t3"]),
        lookalike_distance=int(det["lookalike"]["max_damerau_levenshtein"]),
        account_chars=int(det["lookalike"]["account_prefix_suffix_chars"]),
        dup_window_days=int(det["duplicate_suspect"]["window_days"]),
        dup_distance=int(det["duplicate_suspect"]["reference_max_edit_distance"]),
        split_min_count=int(det["split_pattern"]["min_count"]),
        split_new_payee_min_count=int(det["split_pattern"]["new_payee"]["min_count"]),
        z_threshold=Fraction(Decimal(det["amount_anomaly"]["robust_z_threshold"])),
        z_min_history=int(det["amount_anomaly"]["min_history"]),
    )


@dataclass(frozen=True)
class Outcome:
    verdict: str
    tier: str | None
    score: int
    reason_codes: tuple[str, ...]

    @property
    def executes(self) -> bool:
        return self.verdict in ("ALLOW", "GUARDED")


@dataclass(frozen=True)
class Executed:
    minute: int
    vendor_id: str
    amount_minor: int
    new_payee: bool


@dataclass
class TwinState:
    """Mutable per-(twin, config) state while scenarios are evaluated in sim-time order."""

    twin: Twin
    forecast_min_closing: int
    buffer_minor: int
    reporters: dict[str, int]
    executed: list[Executed] = field(default_factory=list)
    invoices_seen: list[tuple[int, str, str]] = field(default_factory=list)  # (issue_day, vendor_id, ref)

    def __post_init__(self) -> None:
        for inv in self.twin.ap:
            self.invoices_seen.append((inv.issue_day, inv.vendor_id, inv.reference))


def median_exact(values: list[Fraction]) -> Fraction:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def robust_z(history: list[int], amount: int) -> Fraction | None:
    """Robust z-score with exact rationals; None when MAD is zero."""
    med = median_exact([Fraction(h) for h in history])
    mad = median_exact([abs(Fraction(h) - med) for h in history])
    if mad == 0:
        return None
    return abs(Fraction(amount) - med) / (Fraction(14826, 10000) * mad)


def robust_z_exceeds(history: list[int], amount: int, threshold: Fraction) -> bool:
    med = median_exact([Fraction(h) for h in history])
    mad = median_exact([abs(Fraction(h) - med) for h in history])
    dev = abs(Fraction(amount) - med)
    if mad == 0:
        return dev != 0
    return dev / (Fraction(14826, 10000) * mad) > threshold


def payee_new_or_changed(policy: Policy, twin: Twin, v: Vendor, day: int) -> bool:
    w = policy.new_window_days
    if v.account_changed_day is not None and day - v.account_changed_day < w:
        return True
    if day - v.added_day < w:
        return True
    first = twin.first_used_day(v)
    return first is None or day - first < w


def evaluate(  # noqa: PLR0912, PLR0915, PLR0917
    policy: Policy,
    state: TwinState,
    config: str,
    minute: int,
    vendor: Vendor | None,
    amount: int,
    currency: str,
    reference: str,
    invoice_iban: str | None,
    injection: bool,
) -> Outcome:
    twin = state.twin
    day = minute // MINUTES_PER_DAY
    codes: list[str] = []
    if vendor is None:
        codes.append("PAYEE_NOT_IN_REGISTRY")
    if currency != "USD":
        codes.append("CURRENCY_NOT_ALLOWED")
    if amount <= 0:
        codes.append("AMOUNT_INVALID")
    is_new = False
    if vendor is not None:
        nref = normalise_ref(reference)
        if any(
            normalise_ref(p.reference) == nref and p.amount_minor == amount
            for p in twin.paid_ap(vendor.vendor_id)
        ):
            codes.append("DUPLICATE_PAID")
        is_new = payee_new_or_changed(policy, twin, vendor, day)
        window = [e for e in state.executed if minute - 24 * 60 < e.minute <= minute]
        if amount > policy.per_tx_cap:
            codes.append("OVER_PER_TX_CAP")
        if sum(e.amount_minor for e in window) + amount > policy.rolling_24h_cap:
            codes.append("OVER_ROLLING_24H_CAP")
        if is_new and amount > policy.new_payee_cap:
            codes.append("OVER_NEW_PAYEE_CAP")
        if (
            is_new
            and sum(e.amount_minor for e in window if e.new_payee) + amount > policy.new_payee_rolling_24h_cap
        ):
            codes.append("OVER_NEW_PAYEE_ROLLING_24H_CAP")
        spent = sum(e.amount_minor for e in state.executed)
        if state.forecast_min_closing - spent - amount < state.buffer_minor:
            codes.append("CASH_BUFFER_BREACH")
        if config == "B2" and is_new:
            n = state.reporters.get(vendor.iban, 0)
            if n >= 2:
                codes.append("NETWORK_REPORTED_2PLUS")
            elif n == 1:
                codes.append("NETWORK_REPORTED_1")
        if invoice_iban is not None and invoice_iban != vendor.iban:
            codes.append("BANK_DETAIL_MISMATCH")
        others = [v for v in twin.vendors if v.vendor_id != vendor.vendor_id]
        if any(names_lookalike(vendor.name, o.name, policy.lookalike_distance) for o in others):
            codes.append("LOOKALIKE_VENDOR")
        n_chars = policy.account_chars
        mine = bban(vendor.iban)
        if any(
            bban(o.iban)[:n_chars] == mine[:n_chars] or bban(o.iban)[-n_chars:] == mine[-n_chars:]
            for o in others
        ):
            codes.append("LOOKALIKE_ACCOUNT")
        if injection:
            codes.append("INJECTION_MARKERS")
        if any(
            vid == vendor.vendor_id
            and 0 <= day - issue <= policy.dup_window_days
            and damerau_levenshtein(normalise_ref(ref), nref) <= policy.dup_distance
            for issue, vid, ref in state.invoices_seen
        ):
            codes.append("DUPLICATE_SUSPECT")
        same = [e for e in window if e.vendor_id == vendor.vendor_id]
        split_count, split_cap = (
            (policy.split_new_payee_min_count, policy.new_payee_cap)
            if is_new
            else (policy.split_min_count, policy.per_tx_cap)
        )
        if len(same) + 1 >= split_count and sum(e.amount_minor for e in same) + amount > split_cap:
            codes.append("SPLIT_PATTERN")
        history = [p.amount_minor for p in twin.paid_ap(vendor.vendor_id)]
        if len(history) >= policy.z_min_history and robust_z_exceeds(history, amount, policy.z_threshold):
            codes.append("AMOUNT_ANOMALY")
        if is_new:
            codes.append("NEW_OR_CHANGED_PAYEE")

    score = min(policy.score_max, sum(policy.weights.get(c, 0) for c in codes))
    if any(c in policy.hard for c in codes):
        verdict, tier = "DENY", None
    elif any(c in policy.t3 for c in codes) or score >= policy.score_hold_threshold:
        verdict, tier = "HOLD", "T3"
    elif is_new:
        verdict, tier = "GUARDED", "T2"
    elif score <= policy.score_t1_max:
        verdict, tier = "ALLOW", "T1"
    else:
        verdict, tier = "GUARDED", "T2"
    outcome = Outcome(verdict, tier, score, tuple(sorted(codes)))
    if vendor is not None and outcome.executes:
        state.executed.append(Executed(minute, vendor.vendor_id, amount, is_new))
    if vendor is not None:
        state.invoices_seen.append((day, vendor.vendor_id, reference))
    return outcome
