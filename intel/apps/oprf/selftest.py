"""Power-on known-answer test of the OPRF binding (mitigation for the unaudited `voprf` 0.2, D-048).

Before the intel service answers any request it re-runs the CFRG RFC 9497 ristretto255-SHA512 VOPRF
vectors from spec/vectors through apps/oprf/port.py: key derivation, Evaluate, BlindEvaluate and a full
blind / evaluate / verify-proof / finalize round trip, plus one tampered proof that must be rejected.
Any mismatch stops the service (fail closed) instead of serving wrong or unverifiable tags.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .port import OprfClient, OprfError, OprfServer

SUITE = "ristretto255-SHA512"
VOPRF_MODE = 1


class SelfTestError(RuntimeError):
    pass


def _split(hexes: str) -> list[bytes]:
    return [bytes.fromhex(h) for h in hexes.split(",")]


def run_self_test(spec_dir: Path) -> int:
    """Return the number of vector cases checked; raise SelfTestError on any mismatch."""
    data: list[dict[str, Any]] = json.loads(
        (spec_dir / "vectors" / "rfc9497" / "allVectors.json").read_text(encoding="utf-8")
    )
    suites = [s for s in data if s["identifier"] == SUITE and s["mode"] == VOPRF_MODE]
    if len(suites) != 1 or not suites[0]["vectors"]:
        raise SelfTestError("RFC 9497 VOPRF vectors not found")
    suite = suites[0]
    server = OprfServer(bytes.fromhex(suite["seed"]), bytes.fromhex(suite["keyInfo"]))
    if server.public_key.hex() != suite["pkSm"]:
        raise SelfTestError("DeriveKeyPair does not match the RFC 9497 vector")
    client = OprfClient(server.public_key)
    for case in suite["vectors"]:
        inputs, outputs = _split(case["Input"]), case["Output"].split(",")
        if [server.evaluate(x).hex() for x in inputs] != outputs:
            raise SelfTestError("Evaluate does not match the RFC 9497 vector")
        evaluated = server.blind_evaluate(_split(case["BlindedElement"])).evaluated
        if [e.hex() for e in evaluated] != case["EvaluationElement"].split(","):
            raise SelfTestError("BlindEvaluate does not match the RFC 9497 vector")
        state, blinded = client.blind(inputs)
        result = server.blind_evaluate(blinded)
        if [o.hex() for o in client.finalize(state, result.evaluated, result.proof)] != outputs:
            raise SelfTestError("blind/finalize round trip does not match the RFC 9497 vector")
    state, blinded = client.blind(_split(suite["vectors"][0]["Input"]))
    result = server.blind_evaluate(blinded)
    forged = bytes([result.proof[0] ^ 1]) + result.proof[1:]
    try:
        client.finalize(state, result.evaluated, forged)
    except OprfError:
        return len(suite["vectors"])
    raise SelfTestError("a tampered proof was accepted")
