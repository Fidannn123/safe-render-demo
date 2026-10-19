"""RFC 9497 CFRG test vectors for the OPRF port (suite ristretto255-SHA512, VOPRF mode = mode 1).

What the `voprf` 0.2 binding lets us check byte-for-byte against spec/vectors/rfc9497/allVectors.json:
  * DeriveKeyPair(seed, keyInfo) -> pkSm;
  * BlindEvaluate on the vector's BlindedElement(s) -> EvaluationElement(s) (single and batch);
  * Evaluate(Input) -> Output, and a full blind/evaluate/verify/finalize round trip -> Output;
  * the vector Proof encodes/decodes through the binding's proof type.
The binding draws the client blind and the proof nonce r from its own CSPRNG and offers no way to inject
them, so BlindedElement and Proof bytes cannot be reproduced; proofs are instead verified (positive and
negative) through the client. DECISIONS.md D-035 records this coverage.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.conf import settings

from apps.oprf.port import (
    ELEMENT_BYTES,
    OUTPUT_BYTES,
    PROOF_BYTES,
    OprfClient,
    OprfError,
    OprfServer,
    proof_roundtrip,
)

SUITE = "ristretto255-SHA512"


def _suite(mode: int) -> dict[str, Any]:
    path = settings.SAFEPAY_SPEC_DIR / "vectors" / "rfc9497" / "allVectors.json"
    data: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    matches = [s for s in data if s["identifier"] == SUITE and s["mode"] == mode]
    assert len(matches) == 1
    return matches[0]


VOPRF = _suite(1)
CASES = VOPRF["vectors"]


def _split(hexes: str) -> list[bytes]:
    return [bytes.fromhex(h) for h in hexes.split(",")]


def _server() -> OprfServer:
    return OprfServer(bytes.fromhex(VOPRF["seed"]), bytes.fromhex(VOPRF["keyInfo"]))


def test_vector_file_has_batch_case() -> None:
    assert sorted(c["Batch"] for c in CASES) == [1, 1, 2]


def test_derive_key_pair_public_key() -> None:
    assert _server().public_key.hex() == VOPRF["pkSm"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"batch{c['Batch']}-{c['Input'][:8]}")
def test_blind_evaluate_matches_evaluation_element(case: dict[str, Any]) -> None:
    result = _server().blind_evaluate(_split(case["BlindedElement"]))
    assert [e.hex() for e in result.evaluated] == case["EvaluationElement"].split(",")
    assert len(result.proof) == PROOF_BYTES


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"batch{c['Batch']}-{c['Input'][:8]}")
def test_evaluate_matches_output(case: dict[str, Any]) -> None:
    server = _server()
    outputs = [server.evaluate(x).hex() for x in _split(case["Input"])]
    assert outputs == case["Output"].split(",")


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"batch{c['Batch']}-{c['Input'][:8]}")
def test_round_trip_matches_output_and_verifies_proof(case: dict[str, Any]) -> None:
    server = _server()
    client = OprfClient(server.public_key)
    state, blinded = client.blind(_split(case["Input"]))
    assert all(len(b) == ELEMENT_BYTES for b in blinded)
    result = server.blind_evaluate(blinded)
    outputs = client.finalize(state, result.evaluated, result.proof)
    assert [o.hex() for o in outputs] == case["Output"].split(",")
    assert all(len(o) == OUTPUT_BYTES for o in outputs)


@pytest.mark.parametrize("case", CASES, ids=lambda c: f"batch{c['Batch']}-{c['Input'][:8]}")
def test_vector_proof_encoding_round_trips(case: dict[str, Any]) -> None:
    proof = bytes.fromhex(case["Proof"]["proof"])
    assert proof_roundtrip(proof) == proof


def test_proof_from_other_key_is_rejected() -> None:
    server = _server()
    other = OprfServer(b"\x01" * 32, b"other key")
    client = OprfClient(server.public_key)
    state, blinded = client.blind([b"\x00"])
    forged = other.blind_evaluate(blinded)
    with pytest.raises(OprfError):
        client.finalize(state, forged.evaluated, forged.proof)


def test_tampered_proof_is_rejected() -> None:
    server = _server()
    client = OprfClient(server.public_key)
    state, blinded = client.blind([b"\x00", b"\x01"])
    result = server.blind_evaluate(blinded)
    bad = bytes([result.proof[0] ^ 1]) + result.proof[1:]
    with pytest.raises(OprfError):
        client.finalize(state, result.evaluated, bad)


def test_swapped_elements_are_rejected() -> None:
    server = _server()
    client = OprfClient(server.public_key)
    state, blinded = client.blind([b"\x00", b"\x01"])
    result = server.blind_evaluate(blinded)
    with pytest.raises(OprfError):
        client.finalize(state, list(reversed(result.evaluated)), result.proof)


@pytest.mark.parametrize("element", [b"\xff" * 32, b"\x00" * 32, b"\x01" * 31])
def test_invalid_blinded_element_is_rejected(element: bytes) -> None:
    with pytest.raises(OprfError):
        _server().blind_evaluate([element])


def test_empty_batch_and_empty_input_rejected() -> None:
    with pytest.raises(OprfError):
        _server().blind_evaluate([])
    with pytest.raises(OprfError):
        OprfClient(_server().public_key).blind([b""])


def test_bad_seed_and_public_key_rejected() -> None:
    with pytest.raises(OprfError):
        OprfServer(b"\x00" * 31, b"")
    with pytest.raises(OprfError):
        OprfClient(b"\xff" * 32)


def test_blinding_is_randomised() -> None:
    client = OprfClient(_server().public_key)
    _, first = client.blind([b"\x00"])
    _, second = client.blind([b"\x00"])
    assert first != second
