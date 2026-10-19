"""errors.yaml is consistent with Problem.json; official vectors are intact and verify."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
import rfc8785
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .conftest import SPEC, load_schema

VECTORS = SPEC / "vectors"
BUILD_PLAN_CODES = {
    "CAP_EXCEEDED",
    "IDEMPOTENCY_MISMATCH",
    "SELF_APPROVAL",
    "STEP_UP_REQUIRED",
    "TENANT_FROZEN",
    "ILLEGAL_TRANSITION",
    "DECISION_EXPIRED",
    "DECISION_REPLAYED",
    "QUOTA_EXCEEDED",
    "RECEIPT_INVALID",
}


def test_error_codes(errors: Any) -> None:
    codes = errors["codes"]
    assert set(codes) >= BUILD_PLAN_CODES
    assert set(load_schema("Problem.json")["properties"]["code"]["enum"]) == set(codes)
    assert errors["type_prefix"] == "urn:safepay:problem:"
    for code, meta in codes.items():
        assert code.isupper()
        assert 400 <= meta["status"] <= 599
        assert meta["title"]
        assert set(meta["services"]) <= {"core", "intel", "bank"}


def test_vector_sources_unchanged() -> None:
    for line in (VECTORS / "SOURCES.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split()[:2]
        if rel == "ed25519/rfc8032.json":  # parsed file; its source hash is stored inside
            doc = json.loads((VECTORS / rel).read_text(encoding="utf-8"))
            assert doc["source_sha256"] == digest
            continue
        assert hashlib.sha256((VECTORS / rel).read_bytes()).hexdigest() == digest, rel


def test_rfc9497_ristretto_voprf_vectors_present() -> None:
    suites = json.loads((VECTORS / "rfc9497" / "allVectors.json").read_text(encoding="utf-8"))
    voprf = [s for s in suites if s["identifier"] == "ristretto255-SHA512" and s["mode"] == 1]
    assert len(voprf) == 1
    suite = voprf[0]
    assert {"skSm", "pkSm", "seed", "keyInfo", "vectors"} <= set(suite)
    assert len(suite["vectors"]) == 3
    for v in suite["vectors"]:
        assert {"Input", "Blind", "BlindedElement", "EvaluationElement", "Proof", "Output", "Batch"} <= set(v)


def ed25519_vectors() -> list[dict[str, str]]:
    doc = json.loads((VECTORS / "ed25519" / "rfc8032.json").read_text(encoding="utf-8"))
    vectors: list[dict[str, str]] = doc["vectors"]
    return vectors


@pytest.mark.parametrize("vec", ed25519_vectors(), ids=lambda v: v["name"])
def test_rfc8032_vectors_verify_with_pyca(vec: dict[str, str]) -> None:
    sk = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(vec["secret_key"]))
    msg = bytes.fromhex(vec["message"])
    sig = bytes.fromhex(vec["signature"])
    assert sk.public_key().public_bytes_raw().hex() == vec["public_key"]
    assert sk.sign(msg) == sig  # Ed25519 is deterministic
    Ed25519PublicKey.from_public_bytes(bytes.fromhex(vec["public_key"])).verify(sig, msg)


JCS_CASES = sorted(p.stem for p in (VECTORS / "rfc8785" / "input").glob("*.json"))


@pytest.mark.parametrize("case", JCS_CASES)
def test_rfc8785_cases_with_rfc8785_lib(case: str) -> None:
    raw = (VECTORS / "rfc8785" / "input" / f"{case}.json").read_text(encoding="utf-8")
    expected = (VECTORS / "rfc8785" / "output" / f"{case}.json").read_bytes()
    assert rfc8785.dumps(json.loads(raw)) == expected.rstrip(b"\n")
