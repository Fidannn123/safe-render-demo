"""OPRF port: the ONLY place SafePay touches the `voprf` binding (RFC 9497, ristretto255-SHA512, VOPRF mode).

Thin wrapper, no cryptography of its own: key derivation (DeriveKeyPair), BlindEvaluate with a batched DLEQ
proof, Evaluate, Blind and Finalize-with-proof-verification are all done by the `voprf` 0.2 binding over
Meta's Rust `voprf` crate. This module only converts between bytes and the binding's types, fixes the
suite, and turns the binding's errors (including Rust panics surfaced as pyo3 PanicException, which
derives from BaseException) into OprfError.

Wire layout of the binding's VerifiableBatchOutput (checked by tests/test_oprf_vectors.py):
    proof (64 bytes: c || s) || evaluated element 1 (32 bytes) || ... || evaluated element n (32 bytes)

Proven against the CFRG vectors in spec/vectors/rfc9497/allVectors.json (see the test module docstring for
exactly which values are byte-checked). HUMAN REVIEW REQUIRED (crypto wrapper).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from voprf import ristretto as _r

SUITE = "ristretto255-SHA512"
MODE = "VOPRF"
ELEMENT_BYTES = 32
PROOF_BYTES = 64
OUTPUT_BYTES = 64
SEED_BYTES = 32
MAX_BATCH = 16  # spec/openapi-intel.yaml EvaluateRequest.blinded maxItems


class OprfError(Exception):
    """Any failure inside the OPRF binding: bad element, bad key, proof verification failure."""


def _call[T](fn: Callable[..., T], *args: Any) -> T:
    """Call into the binding, mapping every binding failure (incl. Rust panics) to OprfError."""
    try:
        return fn(*args)
    except (KeyboardInterrupt, SystemExit, GeneratorExit):
        raise
    except BaseException as exc:  # pyo3 PanicException is a BaseException subclass
        raise OprfError(f"{type(exc).__name__}: {exc}") from exc


def _check_batch(items: Sequence[bytes], size: int | None, what: str) -> None:
    if not 1 <= len(items) <= MAX_BATCH:
        raise OprfError(f"{what}: batch size must be 1..{MAX_BATCH}")
    for item in items:
        if not isinstance(item, bytes) or (size is not None and len(item) != size):
            raise OprfError(f"{what}: each item must be {size} bytes")


@dataclass(frozen=True)
class EvaluationResult:
    evaluated: list[bytes]
    proof: bytes


class OprfServer:
    """Server side for one epoch key, derived from (seed, key_info) per RFC 9497 DeriveKeyPair."""

    def __init__(self, seed: bytes, key_info: bytes) -> None:
        if len(seed) != SEED_BYTES:
            raise OprfError(f"seed must be {SEED_BYTES} bytes")
        self._evaluator: Any = _call(_r.Evaluator.from_seed, seed, key_info)
        self._public_key: bytes = _call(self._evaluator.public_key.serialize)

    @property
    def public_key(self) -> bytes:
        """pkSm, serialized ristretto255 element (32 bytes)."""
        return self._public_key

    def blind_evaluate(self, blinded: Sequence[bytes]) -> EvaluationResult:
        """RFC 9497 BlindEvaluate over a batch, with one batched DLEQ proof against public_key."""
        _check_batch(blinded, ELEMENT_BYTES, "blinded")
        inputs = [_call(_r.BlindedInput.deserialize, b) for b in blinded]
        raw: bytes = _call(_call(self._evaluator.evaluate_batch, inputs).serialize)
        if len(raw) != PROOF_BYTES + ELEMENT_BYTES * len(blinded):
            raise OprfError("unexpected batch output length from binding")
        proof = raw[:PROOF_BYTES]
        body = raw[PROOF_BYTES:]
        evaluated = [body[i : i + ELEMENT_BYTES] for i in range(0, len(body), ELEMENT_BYTES)]
        return EvaluationResult(evaluated=evaluated, proof=proof)

    def evaluate(self, private_input: bytes) -> bytes:
        """RFC 9497 Evaluate: the 64-byte PRF output for a known input (used to build snapshot tags)."""
        if not private_input:
            raise OprfError("input must not be empty")
        out: bytes = _call(self._evaluator.evaluate_known_input, private_input)
        if len(out) != OUTPUT_BYTES:
            raise OprfError("unexpected output length from binding")
        return out


class BlindState:
    """Opaque client blinding state for one batch (holds the binding's Client objects). Never serialized."""

    __slots__ = ("_clients",)

    def __init__(self, clients: list[Any]) -> None:
        self._clients = clients

    def __len__(self) -> int:
        return len(self._clients)


class OprfClient:
    """Client side pinned to an epoch public key pk_e (obtained from a signed snapshot)."""

    def __init__(self, public_key: bytes) -> None:
        if len(public_key) != ELEMENT_BYTES:
            raise OprfError(f"public key must be {ELEMENT_BYTES} bytes")
        self._public_key: Any = _call(_r.PublicKey.deserialize, public_key)

    def blind(self, inputs: Sequence[bytes]) -> tuple[BlindState, list[bytes]]:
        """RFC 9497 Blind with a fresh random blind per input (binding CSPRNG)."""
        _check_batch(inputs, None, "inputs")
        clients: list[Any] = []
        blinded: list[bytes] = []
        for x in inputs:
            client, blinded_input = _call(_r.Client.blind, x)
            clients.append(client)
            blinded.append(_call(blinded_input.serialize))
        return BlindState(clients), blinded

    def finalize(self, state: BlindState, evaluated: Sequence[bytes], proof: bytes) -> list[bytes]:
        """Verify the batched proof against the pinned public key, unblind, and return the 64-byte outputs."""
        _check_batch(evaluated, ELEMENT_BYTES, "evaluated")
        if len(evaluated) != len(state):
            raise OprfError("evaluated count does not match blinded count")
        if len(proof) != PROOF_BYTES:
            raise OprfError(f"proof must be {PROOF_BYTES} bytes")
        output = _call(_r.VerifiableBatchOutput.deserialize, proof + b"".join(evaluated))
        results: list[bytes] = _call(_r.Client.finalize_batch, state._clients, output, self._public_key)
        if len(results) != len(state) or any(len(o) != OUTPUT_BYTES for o in results):
            raise OprfError("unexpected finalize output from binding")
        return [bytes(o) for o in results]


def proof_roundtrip(proof: bytes) -> bytes:
    """Decode and re-encode a proof through the binding (used to check the vector Proof encoding)."""
    if len(proof) != PROOF_BYTES:
        raise OprfError(f"proof must be {PROOF_BYTES} bytes")
    encoded: bytes = _call(_call(_r.Proof.deserialize, proof).serialize)
    return encoded
