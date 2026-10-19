"""Guardian policy loader: reads spec/policy.yaml (FROZEN) into a typed, immutable Policy.

The loader validates the structural invariants decide() relies on and fails loudly if the file does
not match them: reason-code sets are disjoint, network codes are never hard (never DENY), and the
verdict rules are exactly R1-R5 in the documented order (DECISIONS.md D-001).
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

NETWORK_CODES = frozenset({"NETWORK_UNAVAILABLE", "NETWORK_REPORTED_2PLUS", "NETWORK_REPORTED_1"})

# (id, verdict, tier) exactly as spec/policy.yaml verdict_rules; the predicates live in decide.py.
EXPECTED_VERDICT_RULES: tuple[tuple[str, str, str | None], ...] = (
    ("R1", "DENY", None),
    ("R2", "HOLD", "T3"),
    ("R3", "GUARDED", "T2"),
    ("R4", "ALLOW", "T1"),
    ("R5", "GUARDED", "T2"),
)


class PolicyError(ValueError):
    """spec/policy.yaml does not satisfy an invariant the Guardian depends on."""


@dataclass(frozen=True, slots=True)
class VerdictRule:
    id: str
    when: str
    verdict: str
    tier: str | None


@dataclass(frozen=True, slots=True)
class Policy:
    policy_version: str
    allowed_currencies: frozenset[str]
    per_tx_cap: int
    rolling_24h_cap: int
    new_payee_cap: int
    new_payee_rolling_24h_cap: int
    two_approver_threshold: int
    rolling_window_hours: int
    new_or_changed_window_days: int
    score_max: int
    score_hold_threshold: int
    score_t1_max: int
    decision_ttl_seconds: int
    lookalike_max_damerau_levenshtein: int
    lookalike_account_prefix_suffix_chars: int
    duplicate_window_days: int
    duplicate_reference_max_edit_distance: int
    split_min_count: int
    split_window_hours: int
    split_sum_exceeds: int
    split_new_payee_min_count: int
    split_new_payee_sum_exceeds: int
    anomaly_robust_z_threshold: Decimal
    anomaly_min_history: int
    hard_codes: tuple[str, ...]
    t3_codes: tuple[str, ...]
    signal_weights: MappingProxyType[str, int]
    verdict_rules: tuple[VerdictRule, ...]
    warnings_requiring_ack: tuple[str, ...]
    reapply_agent_caps: bool

    @property
    def all_codes(self) -> tuple[str, ...]:
        return (*self.hard_codes, *self.t3_codes, *self.signal_weights.keys())

    def weight(self, code: str) -> int:
        """Score weight of a reason code; hard and T3 codes weigh 0 (policy.yaml reason_codes)."""
        return self.signal_weights.get(code, 0)


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise PolicyError(f"{name} must be an integer, got {value!r}")
    return value


def parse_policy(raw: dict[str, Any]) -> Policy:
    caps = raw["caps"]
    det = raw["detectors"]
    codes = raw["reason_codes"]
    hard = tuple(str(c) for c in codes["hard"])
    t3 = tuple(str(c) for c in codes["t3"])
    weights = {str(k): _int(v, f"weight {k}") for k, v in codes["signals"].items()}
    if len({*hard, *t3, *weights}) != len(hard) + len(t3) + len(weights):
        raise PolicyError("reason code groups overlap")
    if NETWORK_CODES & set(hard) or not raw["network"]["never_deny"]:
        raise PolicyError("network codes must never DENY")
    if any(weights.get(c, 0) for c in (*hard, *t3)):
        raise PolicyError("hard and T3 codes must not carry weights")
    rules = tuple(
        VerdictRule(id=str(r["id"]), when=str(r["when"]), verdict=str(r["verdict"]), tier=r["tier"])
        for r in raw["verdict_rules"]
    )
    if tuple((r.id, r.verdict, r.tier) for r in rules) != EXPECTED_VERDICT_RULES:
        raise PolicyError("verdict_rules must be exactly R1-R5 (DECISIONS.md D-001)")
    split_cap_key = str(det["split_pattern"]["sum_exceeds"])
    split_new = det["split_pattern"]["new_payee"]
    split_new_cap_key = str(split_new["sum_exceeds"])
    approvals = raw["approvals"]
    return Policy(
        policy_version=str(raw["policy_version"]),
        allowed_currencies=frozenset(str(c) for c in raw["currency"]["allowed"]),
        per_tx_cap=_int(caps["per_tx_cap"], "per_tx_cap"),
        rolling_24h_cap=_int(caps["rolling_24h_cap"], "rolling_24h_cap"),
        new_payee_cap=_int(caps["new_payee_cap"], "new_payee_cap"),
        new_payee_rolling_24h_cap=_int(caps["new_payee_rolling_24h_cap"], "new_payee_rolling_24h_cap"),
        two_approver_threshold=_int(caps["two_approver_threshold"], "two_approver_threshold"),
        rolling_window_hours=_int(caps["rolling_window_hours"], "rolling_window_hours"),
        new_or_changed_window_days=_int(raw["payee"]["new_or_changed_window_days"], "window_days"),
        score_max=_int(raw["scoring"]["score_max"], "score_max"),
        score_hold_threshold=_int(raw["scoring"]["score_hold_threshold"], "score_hold_threshold"),
        score_t1_max=_int(raw["scoring"]["score_t1_max"], "score_t1_max"),
        decision_ttl_seconds=_int(raw["timing"]["decision_ttl_seconds"], "decision_ttl_seconds"),
        lookalike_max_damerau_levenshtein=_int(det["lookalike"]["max_damerau_levenshtein"], "max_dl"),
        lookalike_account_prefix_suffix_chars=_int(
            det["lookalike"]["account_prefix_suffix_chars"], "account_prefix_suffix_chars"
        ),
        duplicate_window_days=_int(det["duplicate_suspect"]["window_days"], "duplicate window_days"),
        duplicate_reference_max_edit_distance=_int(
            det["duplicate_suspect"]["reference_max_edit_distance"], "reference_max_edit_distance"
        ),
        split_min_count=_int(det["split_pattern"]["min_count"], "split min_count"),
        split_window_hours=_int(det["split_pattern"]["window_hours"], "split window_hours"),
        split_sum_exceeds=_int(caps[split_cap_key], f"split sum_exceeds {split_cap_key}"),
        split_new_payee_min_count=_int(split_new["min_count"], "split new_payee min_count"),
        split_new_payee_sum_exceeds=_int(caps[split_new_cap_key], f"split new_payee {split_new_cap_key}"),
        anomaly_robust_z_threshold=Decimal(str(det["amount_anomaly"]["robust_z_threshold"])),
        anomaly_min_history=_int(det["amount_anomaly"]["min_history"], "min_history"),
        hard_codes=hard,
        t3_codes=t3,
        signal_weights=MappingProxyType(weights),
        verdict_rules=rules,
        warnings_requiring_ack=tuple(str(w) for w in approvals["warnings_requiring_ack"]),
        reapply_agent_caps=bool(approvals["reapply_agent_caps"]),
    )


def load_policy_file(path: Path) -> Policy:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise PolicyError(f"{path} is not a mapping")
    return parse_policy(raw)


@functools.cache
def load_policy() -> Policy:
    """The active policy from settings.SAFEPAY_SPEC_DIR / policy.yaml (cached per process)."""
    from django.conf import settings  # noqa: PLC0415  (keeps the pure core importable without Django)

    spec_dir: Path = settings.SAFEPAY_SPEC_DIR
    return load_policy_file(spec_dir / "policy.yaml")
