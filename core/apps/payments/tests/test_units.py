"""Pure unit tests: spec loading, startup binding check, conditions, fingerprint, problem codes."""

from __future__ import annotations

import copy
import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings

from apps.ledger.spec import ledger_spec
from apps.payments.conditions import compile_condition, compile_effect
from apps.payments.errors import ProblemError
from apps.payments.fingerprint import invoice_fingerprint, normalise_reference
from apps.payments.models import Payment
from apps.payments.service import validate_spec_bindings
from apps.payments.spec import parse_state_machine, state_machine


def raw_sm() -> dict[str, Any]:
    path = Path(settings.SAFEPAY_SPEC_DIR) / "state_machine.yaml"
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data


def test_state_machine_loaded_from_spec() -> None:
    sm = state_machine()
    raw = raw_sm()
    assert set(sm.states) == set(raw["states"])
    assert {(t["from"], t["to"]) for t in raw["transitions"]} == set(sm.transitions)
    assert sm.chain_continuation("PROPOSED", "CLEARED") == ("RESERVED",)
    assert sm.chain_continuation("HELD", "APPROVED") == ("RESERVED",)
    assert sm.chain_continuation("CLEARED", "RESERVED") == ()
    assert sm.is_terminal("REJECTED", {"reject_reason": "fraud_or_invalid"})
    assert not sm.is_terminal("REJECTED", {"reject_reason": "not_now"})


def test_startup_check_rejects_unknown_guard() -> None:
    raw = copy.deepcopy(raw_sm())
    raw["transitions"][0]["guards"] = ["no_such_guard"]
    with pytest.raises(ValueError, match="no_such_guard"):
        validate_spec_bindings(parse_state_machine(raw))


def test_startup_check_rejects_unknown_condition() -> None:
    raw = copy.deepcopy(raw_sm())
    raw["transitions"][0]["posting_condition"] = "moon_is_full"
    with pytest.raises(ValueError, match="moon_is_full"):
        validate_spec_bindings(parse_state_machine(raw))


def test_parser_rejects_duplicate_and_bad_chain() -> None:
    raw = copy.deepcopy(raw_sm())
    raw["transitions"].append(raw["transitions"][0])
    with pytest.raises(ValueError, match="duplicate"):
        parse_state_machine(raw)
    raw = copy.deepcopy(raw_sm())
    raw["atomic_chains"] = [["RECEIVED", "SETTLED"]]
    with pytest.raises(ValueError, match="atomic chain"):
        parse_state_machine(raw)


def test_conditions_and_effects() -> None:
    p = Payment(vendor_id=uuid.uuid4(), invoice_accrued=True, reject_reason="fraud_or_invalid")
    assert compile_condition("vendor_bound")(p)
    assert compile_condition("invoice_accrued and reject_reason == fraud_or_invalid")(p)
    p.reject_reason = "not_now"
    assert not compile_condition("invoice_accrued and reject_reason == fraud_or_invalid")(p)
    eff = compile_effect("mark_payee_reportable if reject_reason == fraud_or_invalid")
    eff(p)
    assert not p.payee_reportable
    p.reject_reason = "fraud_or_invalid"
    eff(p)
    assert p.payee_reportable
    with pytest.raises(ValueError, match="unknown effect"):
        compile_effect("launch_rockets")


def test_fingerprint() -> None:
    v = uuid.UUID("12345678-1234-4234-8234-123456789abc")
    assert normalise_reference(" inv-001/a ") == "INV001A"
    assert normalise_reference("\uff29\uff2e\uff36\uff10\uff10\uff11") == "INV001"
    expected = hashlib.sha256(f"{v}|INV001|12345|USD".encode()).hexdigest()
    assert invoice_fingerprint(v, "inv 001", 12345, "USD") == expected
    assert invoice_fingerprint(v, "INV-001", 12345, "USD") == expected
    assert invoice_fingerprint(v, "INV-001", 12346, "USD") != expected
    with pytest.raises(ValueError, match="positive"):
        invoice_fingerprint(v, "x", 0, "USD")


def test_problem_codes_come_from_spec() -> None:
    err = ProblemError("ILLEGAL_TRANSITION", "nope")
    assert err.as_problem() == {
        "type": "urn:safepay:problem:ILLEGAL_TRANSITION",
        "title": "Payment state transition not allowed",
        "status": 409,
        "code": "ILLEGAL_TRANSITION",
        "detail": "nope",
    }
    with pytest.raises(ValueError, match="not in spec"):
        ProblemError("MADE_UP_CODE")


def test_ledger_spec_non_negative_accounts() -> None:
    spec = ledger_spec()
    assert set(spec.non_negative_accounts) == {"Bank:available", "Bank:reserved", "Bank:in_transit"}
    assert set(spec.expense_accounts) == {"Expense", "COGS"}
    assert spec.account_for_code(f"AP:{uuid.uuid4()}").key == "AP:<vendor>"
    with pytest.raises(KeyError):
        spec.account_for_code("Cash:petty")
