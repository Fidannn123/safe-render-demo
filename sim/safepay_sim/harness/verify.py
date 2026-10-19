"""Offline verification of an evidence bundle (make verify-evidence). Needs no database or private key.

Checks: every ops record against spec/schemas/EvidenceRecord.json; every Decision signature (tenant keys
exported with the bundle); every bank receipt and statement signature (bank attestation key) and the
statement arithmetic; every tenant audit chain from genesis plus its signed checkpoints; that each
reference in ops.jsonl points at a verified artifact; and that metrics.json equals a fresh recomputation.
"""

from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from . import metrics as metrics_module

REPO = Path(__file__).resolve().parents[3]


class EvidenceError(Exception):
    pass


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def schema_validator(name: str) -> Draft202012Validator:
    root = REPO / "spec" / "schemas"
    resources = [
        (p.name, Resource.from_contents(json.loads(p.read_text(encoding="utf-8"))))
        for p in root.glob("*.json")
    ]
    registry: Registry[Any] = Registry().with_resources(resources)
    return Draft202012Validator(json.loads((root / name).read_text(encoding="utf-8")), registry=registry)


def raw_key(path: Path) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(base64.b64decode(path.read_text(encoding="ascii").strip()))


def check_signature(document: dict[str, Any], key: Ed25519PublicKey, what: str) -> None:
    unsigned = {k: v for k, v in document.items() if k != "sig"}
    try:
        key.verify(base64.b64decode(document["sig"], validate=True), rfc8785.dumps(unsigned))
    except (InvalidSignature, KeyError, ValueError) as exc:
        raise EvidenceError(f"{what}: signature does not verify") from exc


def verify_audit_dir(directory: Path) -> dict[str, int]:
    """Reuse ops/scripts/verify_audit_export.py, the verifier shipped for audit exports."""
    spec = importlib.util.spec_from_file_location(
        "verify_audit_export", REPO / "ops/scripts/verify_audit_export.py"
    )
    if spec is None or spec.loader is None:
        raise EvidenceError("ops/scripts/verify_audit_export.py is missing")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result: dict[str, int] = module.verify(directory)
    return result


def verified_records(run_dir: Path) -> list[dict[str, Any]]:
    records = read_jsonl(run_dir / "ops.jsonl")
    validator = schema_validator("EvidenceRecord.json")
    for record in records:
        errors = sorted(validator.iter_errors(record), key=lambda e: list(e.path))
        if errors:
            raise EvidenceError(f"ops.jsonl seq {record.get('seq')}: {errors[0].message}")
    if [r["seq"] for r in records] != list(range(len(records))):
        raise EvidenceError("ops.jsonl sequence numbers are not 0..n-1")
    return records


def verified_decisions(run_dir: Path) -> list[dict[str, Any]]:
    tenant_keys: dict[str, str] = json.loads(
        (run_dir / "keys" / "tenant-public-keys.json").read_text(encoding="utf-8")
    )
    decisions = read_jsonl(run_dir / "decisions.jsonl")
    for decision in decisions:
        pem = tenant_keys.get(decision["key_id"])
        if pem is None or not decision["key_id"].startswith(f"tenant-{decision['tenant_id']}:decision:"):
            raise EvidenceError(f"decision {decision['decision_id']}: unknown or foreign signing key")
        public = serialization.load_pem_public_key(pem.encode("ascii"))
        if not isinstance(public, Ed25519PublicKey):
            raise EvidenceError("decision keys must be Ed25519")
        check_signature(decision, public, f"decision {decision['decision_id']}")
    return decisions


def verified_bank_documents(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    bank_key = raw_key(run_dir / "keys" / "bank_attestation.pub")
    receipts = read_jsonl(run_dir / "receipts.jsonl")
    for receipt in receipts:
        check_signature(receipt, bank_key, f"receipt {receipt['receipt_id']}")
    statements = read_jsonl(run_dir / "statements.jsonl")
    for statement in statements:
        check_signature(statement, bank_key, f"statement {statement['account_id']}")
        total = statement["opening_balance_minor"] + sum(
            line["amount_minor"] * (1 if line["direction"] == "CREDIT" else -1) for line in statement["lines"]
        )
        if total != statement["closing_balance_minor"]:
            raise EvidenceError(f"statement {statement['account_id']}: lines do not add up")
    return receipts, statements


def verify_references(
    records: list[dict[str, Any]], decisions: list[dict[str, Any]], receipts: list[dict[str, Any]]
) -> None:
    decision_ids = {d["decision_id"] for d in decisions}
    receipt_ids = {r["receipt_id"] for r in receipts}
    for record in records:
        ref = record["refs"]
        if "decision_id" in ref and ref["decision_id"] not in decision_ids:
            raise EvidenceError(f"ops seq {record['seq']}: decision {ref['decision_id']} not in bundle")
        if "receipt_id" in ref and ref["receipt_id"] not in receipt_ids:
            raise EvidenceError(f"ops seq {record['seq']}: receipt {ref['receipt_id']} not in bundle")


def verify_bundle(run_dir: Path, labels_path: Path) -> dict[str, Any]:
    records = verified_records(run_dir)
    decisions = verified_decisions(run_dir)
    receipts, statements = verified_bank_documents(run_dir)
    audit_events = audit_checkpoints = 0
    for directory in sorted(p for p in (run_dir / "audit").iterdir() if p.is_dir()):
        result = verify_audit_dir(directory)
        audit_events += result["events"]
        audit_checkpoints += result["checkpoints"]
    verify_references(records, decisions, receipts)
    labels = json.loads(labels_path.read_text(encoding="utf-8"))
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    privacy = json.loads((run_dir / "privacy.json").read_text(encoding="utf-8"))
    recomputed = metrics_module.dumps(metrics_module.compute(records, labels, run, privacy))
    if (run_dir / "metrics.json").read_text(encoding="utf-8") != recomputed:
        raise EvidenceError("metrics.json differs from a recomputation over ops.jsonl")
    attack = privacy.get("operator_attack")
    if attack is not None and not (
        attack["passed"]
        and attack["recovered_identifiers"] == 0
        and attack["control_recovered_identifiers"] == attack["true_payees"]
    ):
        raise EvidenceError("the operator privacy attack recovered identifiers or its control failed")
    return {
        "verified": True,
        "ops_records": len(records),
        "decisions": len(decisions),
        "receipts": len(receipts),
        "statements": len(statements),
        "audit_events": audit_events,
        "audit_checkpoints": audit_checkpoints,
        "metrics_recomputed": True,
    }
