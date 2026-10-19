"""JSON Schemas are valid 2020-12, resolve locally, and their examples validate."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from .conftest import SCHEMA_BASE, load_schema, schema_files

EXPECTED = {
    "common",
    "Invoice",
    "ExtractedInvoice",
    "Proposal",
    "Decision",
    "Receipt",
    "Statement",
    "Report",
    "Snapshot",
    "AuditEvent",
    "Checkpoint",
    "EvidenceRecord",
    "Problem",
}


def validator_for(name: str, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(
        {"$ref": SCHEMA_BASE + name}, registry=registry, format_checker=FormatChecker()
    )


def test_expected_schema_set() -> None:
    assert {p.stem for p in schema_files()} == EXPECTED


@pytest.mark.parametrize("path", schema_files(), ids=lambda p: p.name)
def test_schema_is_valid_2020_12(path: Any) -> None:
    schema = load_schema(path.name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


@pytest.mark.parametrize("path", [p for p in schema_files() if p.stem != "common"], ids=lambda p: p.name)
def test_examples_validate(path: Any, registry: Registry) -> None:
    schema = load_schema(path.name)
    examples = schema.get("examples", [])
    assert examples, f"{path.name} must carry at least one example"
    v = validator_for(path.name, registry)
    for example in examples:
        errors = sorted(v.iter_errors(example), key=lambda e: list(e.path))
        assert not errors, [f"{list(e.path)}: {e.message}" for e in errors]


@pytest.mark.parametrize(
    "path", [p for p in schema_files() if p.stem not in {"common", "Problem"}], ids=lambda p: p.name
)
def test_top_level_objects_are_closed(path: Any) -> None:
    schema = load_schema(path.name)
    assert schema.get("additionalProperties") is False
    assert set(schema["required"]) <= set(schema["properties"])


def test_decision_has_exact_architecture_fields() -> None:
    """architecture.md section 6 Decision fields, plus the documented extras (DECISIONS.md D-011)."""
    arch = {
        "decision_id",
        "tenant_id",
        "verdict",
        "tier",
        "payment",
        "score",
        "reasons",
        "policy_version",
        "network_epoch",
        "inputs_sha256",
        "nonce",
        "expires_at",
        "sig",
    }
    extras = {"payment_id", "issued_at", "key_id"}
    schema = load_schema("Decision.json")
    assert set(schema["properties"]) == arch | extras
    assert set(schema["required"]) == arch | extras
    assert set(schema["properties"]["payment"]["required"]) == {
        "vendor_id",
        "account_id",
        "amount_minor",
        "currency",
        "invoice_fingerprint",
    }


@pytest.mark.parametrize(
    ("verdict", "tier", "ok"),
    [
        ("ALLOW", "T1", True),
        ("GUARDED", "T2", True),
        ("HOLD", "T3", True),
        ("DENY", None, True),
        ("ALLOW", "T2", False),
        ("HOLD", "T1", False),
        ("DENY", "T3", False),
        ("GUARDED", None, False),
    ],
)
def test_decision_verdict_tier_binding(verdict: str, tier: str | None, ok: bool, registry: Registry) -> None:
    example = dict(load_schema("Decision.json")["examples"][0], verdict=verdict, tier=tier)
    assert validator_for("Decision.json", registry).is_valid(example) is ok


def test_money_is_never_a_float(registry: Registry) -> None:
    v = validator_for("Proposal.json", registry)
    example = dict(load_schema("Proposal.json")["examples"][0])
    assert v.is_valid(example)
    assert not v.is_valid(dict(example, amount_minor=1250.5))
    assert not v.is_valid(dict(example, amount_minor=0))
    assert not v.is_valid(dict(example, currency="usd"))


def test_untrusted_text_must_be_tagged(registry: Registry) -> None:
    v = validator_for("ExtractedInvoice.json", registry)
    example = load_schema("ExtractedInvoice.json")["examples"][0]
    bad = dict(example, untrusted_text=[{"field": "memo", "value": "x", "provenance": "trusted"}])
    assert not v.is_valid(bad)


def test_no_anchor_in_checkpoint() -> None:
    """AnchorPort dropped (D-030)."""
    assert "anchor" not in load_schema("Checkpoint.json")["properties"]
