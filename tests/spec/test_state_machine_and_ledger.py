"""state_machine.yaml matches architecture.md section 2.2; ledger templates exist and balance."""

from __future__ import annotations

import itertools
from typing import Any

# Oracle: the stateDiagram in docs/architecture.md section 2.2.
ARCH_TRANSITIONS = {
    ("RECEIVED", "EXTRACTED"),
    ("EXTRACTED", "PROPOSED"),
    ("PROPOSED", "DENIED"),
    ("PROPOSED", "HELD"),
    ("PROPOSED", "CLEARED"),
    ("HELD", "APPROVED"),
    ("HELD", "REJECTED"),
    ("APPROVED", "RESERVED"),
    ("CLEARED", "RESERVED"),
    ("RESERVED", "SUBMITTED"),
    ("SUBMITTED", "SETTLED"),
    ("SUBMITTED", "FAILED"),
    ("RESERVED", "FAILED"),
    ("SETTLED", "RECONCILED"),
    ("REJECTED", "PROPOSED"),  # D-004 (accepted with conditions): reject_reason not_now may be re-proposed
}
ARCH_TERMINAL = {"FAILED", "DENIED", "RECONCILED"}
CONDITIONALLY_TERMINAL = {"REJECTED": {"reject_reason": "fraud_or_invalid"}}


def pairs(sm: Any) -> set[tuple[str, str]]:
    return {(t["from"], t["to"]) for t in sm["transitions"]}


def test_transitions_exactly_as_architecture(state_machine: Any) -> None:
    assert pairs(state_machine) == ARCH_TRANSITIONS
    assert len(state_machine["transitions"]) == len(ARCH_TRANSITIONS), "duplicate transition"


def test_states_and_terminals(state_machine: Any, common: dict[str, Any]) -> None:
    states = state_machine["states"]
    assert set(common["$defs"]["PaymentState"]["enum"]) == set(states)
    assert {s for s, v in states.items() if v["terminal"]} == ARCH_TERMINAL
    assert {
        s: v["terminal_when"] for s, v in states.items() if "terminal_when" in v
    } == CONDITIONALLY_TERMINAL
    assert state_machine["initial"] == "RECEIVED"
    for frm, _ in pairs(state_machine):
        assert not states[frm]["terminal"], f"transition out of terminal {frm}"


def test_every_state_reachable(state_machine: Any) -> None:
    edges = pairs(state_machine)
    seen, frontier = {"RECEIVED"}, ["RECEIVED"]
    while frontier:
        cur = frontier.pop()
        for frm, to in edges:
            if frm == cur and to not in seen:
                seen.add(to)
                frontier.append(to)
    assert seen == set(state_machine["states"])


def test_atomic_chains_are_paths(state_machine: Any) -> None:
    edges = pairs(state_machine)
    for chain in state_machine["atomic_chains"]:
        for a, b in itertools.pairwise(chain):
            assert (a, b) in edges


def test_postings_reference_existing_templates(state_machine: Any, ledger: Any) -> None:
    templates = ledger["templates"]
    for t in state_machine["transitions"]:
        if t["posting"] is not None:
            assert t["posting"] in templates, t


def test_money_moving_postings(state_machine: Any) -> None:
    """architecture.md section 2.4 trigger column."""
    by_pair = {(t["from"], t["to"]): t["posting"] for t in state_machine["transitions"]}
    assert by_pair[("CLEARED", "RESERVED")] == "reserve"
    assert by_pair[("APPROVED", "RESERVED")] == "reserve"
    assert by_pair[("RESERVED", "SUBMITTED")] == "submit"
    assert by_pair[("SUBMITTED", "SETTLED")] == "settle"
    assert by_pair[("SUBMITTED", "FAILED")] == "release_in_transit"
    assert by_pair[("RESERVED", "FAILED")] == "release_reserved"
    assert by_pair[("RECEIVED", "EXTRACTED")] == "invoice_accepted"


def test_templates_balance_and_use_known_accounts(ledger: Any) -> None:
    accounts = set(ledger["accounts"])
    for name, tpl in ledger["templates"].items():
        sides = [side for _, side in tpl["lines"]]
        assert sides.count("debit") >= 1, name
        assert sides.count("credit") >= 1, name
        assert len(tpl["lines"]) == 2, f"{name}: single amount, one debit + one credit"
        for account, side in tpl["lines"]:
            assert side in {"debit", "credit"}
            if account == "{expense_account}":
                continue
            assert account in accounts, f"{name}: unknown account {account}"


def test_bank_accounts_cannot_go_negative(ledger: Any) -> None:
    for acct in ("Bank:available", "Bank:reserved", "Bank:in_transit"):
        assert ledger["accounts"][acct]["non_negative"] is True
        assert ledger["accounts"][acct]["normal"] == "debit"


def test_architecture_posting_table(ledger: Any) -> None:
    t = {k: [tuple(line) for line in v["lines"]] for k, v in ledger["templates"].items()}
    assert t["invoice_accepted"] == [("{expense_account}", "debit"), ("AP:<vendor>", "credit")]
    assert t["reserve"] == [("Bank:reserved", "debit"), ("Bank:available", "credit")]
    assert t["submit"] == [("Bank:in_transit", "debit"), ("Bank:reserved", "credit")]
    assert t["settle"] == [("AP:<vendor>", "debit"), ("Bank:in_transit", "credit")]
    assert t["release_in_transit"] == [("Bank:available", "debit"), ("Bank:in_transit", "credit")]
    assert t["release_reserved"] == [("Bank:available", "debit"), ("Bank:reserved", "credit")]


def test_rejection_rules(state_machine: Any) -> None:
    """D-004 conditions: fraud_or_invalid reverses the accrual and is final; not_now can be re-proposed."""
    by_pair = {(t["from"], t["to"]): t for t in state_machine["transitions"]}
    reject = by_pair[("HELD", "REJECTED")]
    assert reject["posting"] == "invoice_voided"
    assert "reject_reason == fraud_or_invalid" in reject["posting_condition"]
    assert "reject_reason_valid" in reject["guards"]
    assert any("mark_payee_reportable" in e for e in reject["effects"])
    assert by_pair[("REJECTED", "PROPOSED")]["guards"] == ["reject_reason_not_now"]


def test_final_approval_rechecks(state_machine: Any) -> None:
    """D-029: approval re-checks hard rules, freeze, payee account and funds; never the agent caps."""
    guards = {(t["from"], t["to"]): t.get("guards", []) for t in state_machine["transitions"]}[
        ("HELD", "APPROVED")
    ]
    for g in (
        "hard_rules_pass",
        "tenant_not_frozen",
        "payee_account_unchanged",
        "funds_available",
        "warnings_acknowledged",
        "approver_not_initiator",
        "distinct_approvers",
    ):
        assert g in guards
    assert not [g for g in guards if "cap" in g]
