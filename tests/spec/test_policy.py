"""policy.yaml holds exactly the BUILD_PLAN.md defaults and the documented verdict resolution."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# Oracle: docs/BUILD_PLAN.md "Default policy values", as changed by DECISIONS.md D-045. Typed here on
# purpose: the test is the check.
DEFAULT_WEIGHTS = {
    "BANK_DETAIL_MISMATCH": 50,
    "LOOKALIKE_VENDOR": 50,
    "LOOKALIKE_ACCOUNT": 50,
    "NETWORK_REPORTED_2PLUS": 50,
    "NETWORK_REPORTED_1": 35,
    "INJECTION_MARKERS": 30,
    "DUPLICATE_SUSPECT": 30,
    "SPLIT_PATTERN": 35,
    "AMOUNT_ANOMALY": 20,
    "NEW_OR_CHANGED_PAYEE": 15,
}
HARD = {
    "PAYEE_NOT_IN_REGISTRY",
    "CURRENCY_NOT_ALLOWED",
    "DUPLICATE_PAID",
    "VENDOR_BLOCKED",
    "TENANT_FROZEN",
    "AMOUNT_INVALID",
}


def test_version(policy: Any) -> None:
    assert policy["policy_version"] == "2026.10.19-2"


def test_default_values(policy: Any) -> None:
    caps, timing, net = policy["caps"], policy["timing"], policy["network"]
    assert caps["per_tx_cap"] == 500000
    assert caps["rolling_24h_cap"] == 2000000
    assert caps["new_payee_cap"] == 100000
    assert caps["new_payee_rolling_24h_cap"] == 200000
    assert caps["two_approver_threshold"] == 1500000
    assert caps["rolling_window_hours"] == 24
    assert policy["payee"]["new_or_changed_window_days"] == 30
    assert policy["scoring"]["score_hold_threshold"] == 50
    assert policy["scoring"]["score_t1_max"] == 29
    assert policy["buffer"]["buffer_floor"] == 500000
    assert policy["buffer"]["median_opex_weeks"] == 2
    assert Decimal(policy["scheduling"]["early_pay_hurdle_apr"]) == Decimal("0.12")
    assert timing["t2_cooloff_seconds"] == {"demo": 600, "prod": 14400}
    assert timing["decision_ttl_seconds"] == 900
    assert timing["step_up_otp_max_age_seconds"] == 300
    assert timing["agent_token_ttl_seconds"] == 3600
    assert net["oprf_quota_per_member_per_day"] == 500
    assert net["oprf_epoch_days"] == 7
    assert net["snapshot_pull_seconds"] == 600
    assert net["report_expiry_days"] == 30
    assert net["receipt_max_age_days"] == 90
    assert net["tag_bytes"] == 16
    assert policy["outbox"]["outbox_max_retries"] == 6
    assert policy["recon"]["recon_interval_seconds"] == 300
    assert policy["recon"]["end_of_day"] is True
    assert policy["currency"]["allowed"] == ["USD"]


def test_weights(policy: Any) -> None:
    assert policy["reason_codes"]["signals"] == DEFAULT_WEIGHTS


def test_hard_rules(policy: Any) -> None:
    assert set(policy["reason_codes"]["hard"]) == HARD


def test_reason_code_enum_matches_policy(policy: Any, common: dict[str, Any]) -> None:
    rc = policy["reason_codes"]
    union = set(rc["hard"]) | set(rc["t3"]) | set(rc["signals"])
    groups = [set(rc["hard"]), set(rc["t3"]), set(rc["signals"])]
    assert sum(len(g) for g in groups) == len(union), "a reason code is in two groups"
    assert set(common["$defs"]["ReasonCode"]["enum"]) == union


def test_network_signals_never_deny(policy: Any) -> None:
    rc = policy["reason_codes"]
    network = {c for c in [*rc["hard"], *rc["t3"], *rc["signals"]] if c.startswith("NETWORK_")}
    assert network == {"NETWORK_REPORTED_2PLUS", "NETWORK_REPORTED_1", "NETWORK_UNAVAILABLE"}
    assert not network & set(rc["hard"])
    assert policy["network"]["never_deny"] is True
    assert policy["network"]["on_unavailable"] == {
        "new_or_changed": "NETWORK_UNAVAILABLE",
        "known": "continue",
    }


def test_verdict_rules_follow_resolution(policy: Any) -> None:
    """DECISIONS.md D-001: DENY > HOLD > GUARDED(new) > ALLOW(<30) > GUARDED(30..49)."""
    rules = [(r["id"], r["verdict"], r["tier"]) for r in policy["verdict_rules"]]
    assert rules == [
        ("R1", "DENY", None),
        ("R2", "HOLD", "T3"),
        ("R3", "GUARDED", "T2"),
        ("R4", "ALLOW", "T1"),
        ("R5", "GUARDED", "T2"),
    ]
    assert policy["reserve_on"] == ["ALLOW", "GUARDED"]


def test_no_floats_in_policy(policy: Any) -> None:
    def walk(node: Any, path: str) -> None:
        assert not isinstance(node, float), f"float at {path}; use int minor units or a decimal string"
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(policy, "policy")


def test_approval_policy(policy: Any) -> None:
    """D-029 and D-007 (revised): human approval semantics."""
    a = policy["approvals"]
    assert a["reapply_agent_caps"] is False
    assert a["approvers_required_at_or_above_two_approver_threshold"] == 2
    assert a["recheck_at_final_approval"] == [
        "hard_rules",
        "tenant_not_frozen",
        "payee_account_unchanged",
        "funds_available",
    ]
    assert a["warnings_requiring_ack"] == ["CASH_BUFFER_BREACH"]
    assert a["approved_amounts_count_toward_rolling_caps"] is True
    assert a["audit_fields"] == ["approver_ids", "reasons", "acknowledged_warnings"]
    assert a["reject_reasons"] == {
        "fraud_or_invalid": {"reverse_accrual": True, "payee_reportable": True, "reproposable": False},
        "not_now": {"reverse_accrual": False, "payee_reportable": False, "reproposable": True},
    }
