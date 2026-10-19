"""recheck_for_approval (D-029): typed failures and warnings; agent caps are never re-applied."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml
from django.conf import settings

from apps.guardian.decide import IssuedDecision, issue_decision
from apps.guardian.inputs import ForecastBuffer, HistoryPayment, RegistryAccount, VendorSnapshot
from apps.guardian.policy import Policy, load_policy
from apps.guardian.recheck import (
    ApprovalContext,
    InputsIntegrityError,
    RecheckFailure,
    recheck_for_approval,
)
from apps.keystore.file_store import FileKeyStore

from .factories import FINGERPRINT, NOW, base, build, history_item

LATER = NOW + dt.timedelta(minutes=30)


@pytest.fixture(scope="module")
def policy() -> Policy:
    return load_policy()


def held_over_cap(tmp_path: Path, policy: Policy, **changes: Any) -> IssuedDecision:
    d = base()
    d["proposal"]["amount_minor"] = d["extraction"]["amount_minor"] = 900000  # OVER_PER_TX_CAP
    d["rolling"]["outflow_24h_minor"] = 1900000  # and OVER_ROLLING_24H_CAP
    for key, value in changes.items():
        d[key] = value
    issued = issue_decision(build(d), policy, FileKeyStore(tmp_path))
    assert issued.decision.verdict == "HOLD"
    return issued


def context(issued: IssuedDecision, **overrides: Any) -> ApprovalContext:
    bound = build(base())
    values: dict[str, Any] = {
        "vendor": bound.vendor,
        "registry_account": bound.registry_account,
        "history": (),
        "tenant_frozen": False,
        "bank_available_minor": 10_000_000,
        "now": LATER,
        "forecast": None,
        "acknowledged_warnings": (),
    }
    values.update(overrides)
    return ApprovalContext(**values)


def run(issued: IssuedDecision, ctx: ApprovalContext, policy: Policy) -> Any:
    return recheck_for_approval(issued.canonical_inputs, issued.decision.inputs_sha256, ctx, policy)


def test_caps_are_not_reapplied(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    result = run(issued, context(issued), policy)
    assert result.ok
    assert result.failures == ()
    assert result.error_code is None
    assert policy.reapply_agent_caps is False


def test_frozen(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    result = run(issued, context(issued, tenant_frozen=True), policy)
    assert result.failures == (RecheckFailure.TENANT_FROZEN,)
    assert result.failed_hard_rules == ()


def test_vendor_blocked_is_hard_rule_failure(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    vendor = VendorSnapshot.model_validate({**base()["vendor"], "blocked": True})
    result = run(issued, context(issued, vendor=vendor), policy)
    assert result.error_code is RecheckFailure.HARD_RULE_FAILED
    assert result.failed_hard_rules == ("VENDOR_BLOCKED",)


def test_duplicate_paid_since_decision(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    dup = HistoryPayment.model_validate(
        history_item(1, invoice_fingerprint=FINGERPRINT, state="SETTLED", created_at="2026-10-19T08:10:00Z")
    )
    result = run(issued, context(issued, history=(dup,)), policy)
    assert result.failed_hard_rules == ("DUPLICATE_PAID",)


@pytest.mark.parametrize(
    "change",
    [
        {"iban": "GB33BUKB20201555555555"},
        {"country": "IE"},
        {"changed_at": "2026-10-19T08:20:00Z"},
        {"account_id": "0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9"},
    ],
    ids=["iban", "country", "changed_at", "account_id"],
)
def test_payee_changed(tmp_path: Path, policy: Policy, change: dict[str, Any]) -> None:
    issued = held_over_cap(tmp_path, policy)
    account = RegistryAccount.model_validate({**base()["registry_account"], **change})
    result = run(issued, context(issued, registry_account=account), policy)
    assert RecheckFailure.PAYEE_CHANGED in result.failures


def test_payee_removed_is_changed_and_hard(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    result = run(issued, context(issued, registry_account=None), policy)
    assert result.failures[:1] == (RecheckFailure.HARD_RULE_FAILED,)
    assert RecheckFailure.PAYEE_CHANGED in result.failures
    assert result.failed_hard_rules == ("PAYEE_NOT_IN_REGISTRY",)


def test_first_use_alone_is_not_a_change(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    account = RegistryAccount.model_validate(
        {**base()["registry_account"], "first_used_at": "2026-10-19T08:05:00Z"}
    )
    assert run(issued, context(issued, registry_account=account), policy).ok


def test_funds_unavailable(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    assert run(issued, context(issued, bank_available_minor=900000), policy).ok
    result = run(issued, context(issued, bank_available_minor=899999), policy)
    assert result.failures == (RecheckFailure.FUNDS_UNAVAILABLE,)


def test_cash_buffer_warning_needs_ack(tmp_path: Path, policy: Policy) -> None:
    breach = {"buffer_minor": 500000, "projected_min_balance_minor": 100}
    issued = held_over_cap(tmp_path, policy, forecast=breach)
    result = run(issued, context(issued), policy)
    assert result.warnings == ("CASH_BUFFER_BREACH",)
    assert result.failures == (RecheckFailure.WARNING_NOT_ACKNOWLEDGED,)
    acked = run(issued, context(issued, acknowledged_warnings=("CASH_BUFFER_BREACH",)), policy)
    assert acked.ok
    assert acked.warnings == ("CASH_BUFFER_BREACH",)


def test_current_forecast_overrides_bound(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    now_breach = ForecastBuffer(buffer_minor=500000, projected_min_balance_minor=0)
    result = run(issued, context(issued, forecast=now_breach), policy)
    assert result.unacknowledged_warnings == ("CASH_BUFFER_BREACH",)


def test_failure_order_follows_policy(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    vendor = VendorSnapshot.model_validate({**base()["vendor"], "blocked": True})
    account = RegistryAccount.model_validate({**base()["registry_account"], "iban": "GB33BUKB20201555555555"})
    result = run(
        issued,
        context(
            issued,
            vendor=vendor,
            registry_account=account,
            tenant_frozen=True,
            bank_available_minor=0,
            forecast=ForecastBuffer(buffer_minor=1, projected_min_balance_minor=0),
        ),
        policy,
    )
    assert result.failures == (
        RecheckFailure.HARD_RULE_FAILED,
        RecheckFailure.TENANT_FROZEN,
        RecheckFailure.PAYEE_CHANGED,
        RecheckFailure.FUNDS_UNAVAILABLE,
        RecheckFailure.WARNING_NOT_ACKNOWLEDGED,
    )


def test_failure_codes_exist_in_errors_yaml() -> None:
    errors = yaml.safe_load((Path(settings.SAFEPAY_SPEC_DIR) / "errors.yaml").read_text(encoding="utf-8"))
    codes = set(errors["codes"])
    assert {f.value for f in RecheckFailure} <= codes


def test_tampered_stored_inputs_rejected(tmp_path: Path, policy: Policy) -> None:
    issued = held_over_cap(tmp_path, policy)
    tampered = issued.canonical_inputs.replace(b"GB82WEST12345698765432", b"GB33BUKB20201555555555")
    with pytest.raises(InputsIntegrityError):
        recheck_for_approval(tampered, issued.decision.inputs_sha256, context(issued), policy)
