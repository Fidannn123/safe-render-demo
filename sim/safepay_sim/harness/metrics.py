"""Every reported number is computed here from ops.jsonl (and the labels); none is typed by hand.

`make verify-evidence` recomputes this function's output and compares it with metrics.json byte for byte.
Rates are rounded to 4 decimals and latencies to 3, so the comparison is exact.
"""

from __future__ import annotations

import json
import math
from typing import Any

CONFIGS = ("B0", "B1", "B2")
TASKS = ("PAY_INVOICE", "SCHEDULE_PAYABLES", "CASHFLOW_ALERT", "FINANCIAL_ANALYSIS", "FINANCING_MATCH")
DISCLOSURE = (
    "SYNTHETIC data, SIMULATED bank, deterministic mock LLM. Every number is computed from ops.jsonl "
    "of this run by safepay_sim.harness.metrics and re-checked by make verify-evidence."
)


LIMITATIONS = [
    "All companies, invoices, accounts and amounts are SYNTHETIC (safepay_sim.twins); the bank is SIMULATED.",
    "The LLM is the deterministic mock extractor; a real provider is supported but not measured here.",
    "Attackers are scripted scenario classes, not adaptive adversaries.",
    "Simulated time drives the tenant domain; the bank sandbox and intel network run on wall-clock time.",
    "Network lift is measured on the REPORTED_PAYEE scenarios of this seed only.",
    "T2 payments execute after the cool-off because the harness never vetoes; a watching owner could.",
]


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (no interpolation)."""
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[max(0, math.ceil(q * len(ordered)) - 1)], 3)


def applicable(labels: dict[str, Any], config: str) -> list[dict[str, Any]]:
    """Scenarios a configuration is scored on (B0 is scored on invoices and the stolen token only)."""
    rows = [s for s in labels["scenarios"] if s["expected"][config].get("outcome") != "NOT_APPLICABLE"]
    if config == "B0":
        rows = [s for s in rows if s["invoice"] is not None or s["kind"] == "stolen_token"]
    return rows


def attack_succeeded(record: dict[str, Any]) -> bool:
    return bool(record["loss_minor"] > 0 or record["observed"].get("outcome") == "EXPOSED")


def config_metrics(records: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    attacks = [r for r in records if r["label"] == "attack"]
    benign = [r for r in records if r["label"] == "benign"]
    benign_pay = [r for r in benign if r["task"] == "PAY_INVOICE"]
    held = [
        r
        for r in benign_pay
        if r["observed"].get("verdict") in ("HOLD", "DENY", "BLOCKED_AT_API", "BLOCKED_AT_EXTRACTION")
        and r["expected"].get("verdict") in ("ALLOW", "GUARDED")
    ]
    ambiguous = [r for r in records if r["label"] == "ambiguous"]
    latencies = [r["latency_ms"] for r in records if "latency_ms" in r]
    return {
        "scenarios": len(scenarios),
        "executed": len(records),
        "passed": sum(r["outcome"] == "PASS" for r in records),
        "not_run": len(scenarios) - len(records),
        "attacks": len(attacks),
        "attacks_succeeded": sum(attack_succeeded(r) for r in attacks),
        "attacker_loss_minor": sum(r["loss_minor"] for r in records),
        "attack_success_rate": rate(sum(attack_succeeded(r) for r in attacks), len(attacks)),
        "benign_success_rate": rate(sum(r["outcome"] == "PASS" for r in benign), len(benign)),
        "false_hold_rate": rate(len(held), len(benign_pay)),
        "ambiguous_held_rate": rate(sum(r["outcome"] == "PASS" for r in ambiguous), len(ambiguous)),
        "latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
            "samples": len(latencies),
        },
        "tasks": {
            task: {
                "executed": sum(r["task"] == task and r["label"] == "benign" for r in records),
                "passed": sum(
                    r["task"] == task and r["label"] == "benign" and r["outcome"] == "PASS" for r in records
                ),
            }
            for task in TASKS
        },
    }


def alert_scores(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Precision/recall of LOW_BUFFER/SHORTFALL weeks against the twins' independent forecast oracle."""
    tp = fp = fn = 0
    for r in records:
        if r["task"] != "CASHFLOW_ALERT":
            continue
        expected = r["observed"]["expected_weeks"]
        for kind in ("low_buffer_weeks", "shortfall_weeks"):
            truth, seen = set(expected[kind]), set(r["observed"][kind])
            tp, fp, fn = tp + len(truth & seen), fp + len(seen - truth), fn + len(truth - seen)
    return {
        "true_positive_weeks": tp,
        "false_positive_weeks": fp,
        "false_negative_weeks": fn,
        "precision": rate(tp, tp + fp),
        "recall": rate(tp, tp + fn),
    }


def analysis_accuracy(records: list[dict[str, Any]]) -> dict[str, Any]:
    matched = total = 0
    for r in records:
        if r["task"] == "FINANCIAL_ANALYSIS":
            values = r["observed"]["matched"].values()
            matched, total = matched + sum(values), total + len(values)
    return {"answers": total, "exact_matches": matched, "accuracy": rate(matched, total)}


def compute(
    records: list[dict[str, Any]], labels: dict[str, Any], run: dict[str, Any], privacy: dict[str, Any]
) -> dict[str, Any]:
    by_config = {c: [r for r in records if r["config"] == c] for c in CONFIGS}
    configs = {c: config_metrics(by_config[c], applicable(labels, c)) for c in CONFIGS}
    reported = {s["scenario_id"] for s in labels["scenarios"] if s["scenario_class"] == "REPORTED_PAYEE"}

    def stopped(config: str) -> set[str]:
        return {
            r["scenario_id"]
            for r in by_config[config]
            if r["scenario_id"] in reported and r["loss_minor"] == 0
        }

    lift = rate(len(stopped("B2") - stopped("B1")), len(reported))
    b1 = [
        r
        for r in by_config["B1"]
        if r["task"] in ("SCHEDULE_PAYABLES", "CASHFLOW_ALERT", "FINANCIAL_ANALYSIS", "FINANCING_MATCH")
    ]
    return {
        "run_id": run["run_id"],
        "seed": run["seed"],
        "synthetic": True,
        "mode": f"{run['mode']} mode, LLM={run['llm']}, policy {run['policy_version']}",
        "disclosure": DISCLOSURE,
        "configs": configs,
        "network_detection_lift": lift,
        "network": {
            "reported_payee_scenarios": len(reported),
            "stopped_before_payment_b1": len(stopped("B1")),
            "stopped_before_payment_b2": len(stopped("B2")),
        },
        "cashflow_alerts": alert_scores(b1),
        "financial_analysis": analysis_accuracy(b1),
        "privacy": {
            "captured_query_requests": privacy["captured_query_requests"],
            "query_requests_containing_an_iban": privacy["query_requests_containing_an_iban"],
        },
        "residual_attacks": {
            c: [
                {
                    "scenario_id": r["scenario_id"],
                    "scenario_class": r["scenario_class"],
                    "verdict": r["observed"].get("verdict") or r["observed"].get("outcome"),
                    "tier": r["observed"].get("tier"),
                    "loss_minor": r["loss_minor"],
                }
                for r in by_config[c]
                if r["label"] == "attack" and attack_succeeded(r)
            ]
            for c in CONFIGS
        },
        "limitations": LIMITATIONS,
    }


def dumps(metrics: dict[str, Any]) -> str:
    return json.dumps(metrics, indent=2, sort_keys=True) + "\n"
