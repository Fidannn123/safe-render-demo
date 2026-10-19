"""OpenAPI 3.1 documents are valid, contain every frozen endpoint, and follow the error/idempotency rules."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from openapi_spec_validator import validate
from openapi_spec_validator.readers import read_from_filename

from .conftest import SPEC, load_yaml

DOCS = ["openapi-core.yaml", "openapi-intel.yaml", "openapi-bank.yaml"]
METHODS = {"get", "post", "put", "patch", "delete"}

# architecture.md section 6 "Frozen interfaces" + BUILD_PLAN.md Phase 0 additions + DECISIONS.md D-008/D-009.
REQUIRED_OPERATIONS = {
    "openapi-core.yaml": {
        ("get", "/healthz"),
        ("post", "/api/v1/agent/proposals"),
        ("post", "/api/v1/payments/{payment_id}/approve"),
        ("post", "/api/v1/payments/{payment_id}/reject"),
        ("post", "/api/v1/tenants/{tenant_id}/freeze"),
        ("post", "/api/v1/tenants/{tenant_id}/unfreeze"),
        ("get", "/api/v1/cashflow/forecast"),
        ("get", "/api/v1/analysis/{metric}"),
        ("post", "/api/v1/invoices"),
        ("get", "/api/v1/payments/{payment_id}"),
        ("get", "/api/v1/decisions/{decision_id}"),
        ("get", "/api/v1/alerts"),
        ("post", "/api/v1/financing/match"),
        ("post", "/api/v1/vendors/{vendor_id}/bank-account-change"),
        # D-008 / D-009 additions
        ("post", "/api/v1/payments/{payment_id}/veto"),
        ("post", "/api/v1/vendors/{vendor_id}/bank-account-change/{change_id}/confirm"),
        ("post", "/api/v1/auth/login"),
        ("post", "/api/v1/auth/step-up"),
        ("post", "/api/v1/auth/agent-tokens"),
        ("get", "/api/v1/payments"),
        ("get", "/api/v1/vendors"),
        ("get", "/api/v1/vendors/{vendor_id}"),
        ("get", "/api/v1/cashflow/schedule"),
    },
    "openapi-intel.yaml": {
        ("get", "/healthz"),
        ("post", "/v1/oprf/evaluate"),
        ("get", "/v1/snapshots/{epoch}"),
        ("post", "/v1/reports"),
        ("delete", "/v1/reports/{report_id}"),
        ("get", "/v1/epochs/current"),
    },
    "openapi-bank.yaml": {
        ("get", "/healthz"),
        ("post", "/v1/transfers"),
        ("get", "/v1/transfers/{end_to_end_id}"),
        ("get", "/v1/statements"),
        ("post", "/admin/fail-next"),
    },
}


def load(doc: str) -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load((SPEC / doc).read_text(encoding="utf-8"))
    return data


def operations(doc: str) -> list[tuple[str, str, dict[str, Any]]]:
    return [(m, p, op) for p, item in load(doc)["paths"].items() for m, op in item.items() if m in METHODS]


@pytest.mark.parametrize("doc", DOCS)
def test_openapi_document_is_valid(doc: str) -> None:
    spec_dict, base_uri = read_from_filename(str(SPEC / doc))
    assert spec_dict["openapi"] == "3.1.0"
    validate(spec_dict, base_uri=base_uri)  # resolves ./schemas/*.json refs locally


@pytest.mark.parametrize("doc", DOCS)
def test_required_operations_present(doc: str) -> None:
    present = {(m, p) for m, p, _ in operations(doc)}
    missing = REQUIRED_OPERATIONS[doc] - present
    assert not missing, missing


@pytest.mark.parametrize("doc", DOCS)
def test_operation_ids_unique(doc: str) -> None:
    ids = [op["operationId"] for _, _, op in operations(doc)]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("doc", DOCS)
def test_errors_are_problem_json_with_known_codes(doc: str) -> None:
    codes = load_yaml("errors.yaml")["codes"]
    service = doc.removeprefix("openapi-").removesuffix(".yaml")
    for method, path, op in operations(doc):
        if path == "/healthz":
            continue
        declared = op.get("x-problem-codes")
        assert declared, f"{method} {path}: x-problem-codes missing"
        statuses = {int(s) for s in op["responses"] if s.startswith(("4", "5"))}
        for code in declared:
            assert code in codes, f"{method} {path}: unknown code {code}"
            assert service in codes[code]["services"], f"{code} not declared for {service}"
            assert codes[code]["status"] in statuses, (
                f"{method} {path}: no {codes[code]['status']} for {code}"
            )
        for status, resp in op["responses"].items():
            if status.startswith(("4", "5")):
                assert resp == {"$ref": "#/components/responses/Problem"}, f"{method} {path} {status}"


def test_core_mutating_posts_require_idempotency_key() -> None:
    for method, path, op in operations("openapi-core.yaml"):
        if method != "post":
            continue
        refs = {p.get("$ref") for p in op.get("parameters", [])}
        has_key = "#/components/parameters/IdempotencyKey" in refs
        if op.get("x-idempotency") == "exempt":
            assert not has_key, path
        else:
            assert has_key, f"{path} must require Idempotency-Key"
            assert "IDEMPOTENCY_MISMATCH" in op["x-problem-codes"], path


def test_agent_cannot_reach_config_or_human_endpoints() -> None:
    """Registry edits, approvals, freeze/unfreeze and token issuance are human-only."""
    human_only = {
        "/api/v1/vendors/{vendor_id}/bank-account-change",
        "/api/v1/vendors/{vendor_id}/bank-account-change/{change_id}/confirm",
        "/api/v1/payments/{payment_id}/approve",
        "/api/v1/payments/{payment_id}/reject",
        "/api/v1/payments/{payment_id}/veto",
        "/api/v1/tenants/{tenant_id}/freeze",
        "/api/v1/tenants/{tenant_id}/unfreeze",
        "/api/v1/auth/agent-tokens",
    }
    for method, path, op in operations("openapi-core.yaml"):
        if path in human_only or (path == "/api/v1/vendors" and method == "post"):
            assert "x-agent-scopes" not in op, path
            assert all("agentToken" not in s for s in op["security"]), path


def test_step_up_operations() -> None:
    step_up = {op["operationId"] for _, _, op in operations("openapi-core.yaml") if op.get("x-step-up")}
    assert {
        "payments_approve",
        "payments_reject",
        "payments_veto",
        "tenants_unfreeze",
        "vendor_bank_change_request",
        "vendor_bank_change_confirm",
        "agent_token_issue",
        "vendors_create",
    } <= step_up
    for _, _, op in operations("openapi-core.yaml"):
        if op.get("x-step-up"):
            assert "STEP_UP_REQUIRED" in op["x-problem-codes"], op["operationId"]


def test_proposals_only_for_t1_t2_tokens() -> None:
    op = load("openapi-core.yaml")["paths"]["/api/v1/agent/proposals"]["post"]
    assert op["security"] == [{"agentToken": []}]
    assert op["x-agent-scopes"] == ["T1", "T2"]


def test_approve_and_reject_bodies() -> None:
    doc = load("openapi-core.yaml")
    schemas = doc["components"]["schemas"]
    approve = doc["paths"]["/api/v1/payments/{payment_id}/approve"]["post"]
    reject = doc["paths"]["/api/v1/payments/{payment_id}/reject"]["post"]
    body = "requestBody"
    assert approve[body]["content"]["application/json"]["schema"]["$ref"].endswith("/ApproveBody")
    assert reject[body]["content"]["application/json"]["schema"]["$ref"].endswith("/RejectBody")
    assert "CAP_EXCEEDED" not in approve["x-problem-codes"]  # agent caps are not re-applied (D-029)
    for code in (
        "HARD_RULE_FAILED",
        "PAYEE_CHANGED",
        "FUNDS_UNAVAILABLE",
        "WARNING_NOT_ACKNOWLEDGED",
        "TENANT_FROZEN",
        "SELF_APPROVAL",
    ):
        assert code in approve["x-problem-codes"]
    assert schemas["RejectReason"]["enum"] == ["fraud_or_invalid", "not_now"]
    assert "reject_reason" in schemas["RejectBody"]["required"]


def test_financing_exactly_three_products() -> None:
    opts = load("openapi-core.yaml")["components"]["schemas"]["FinancingMatchResult"]["properties"]["options"]
    assert opts["minItems"] == opts["maxItems"] == 3
