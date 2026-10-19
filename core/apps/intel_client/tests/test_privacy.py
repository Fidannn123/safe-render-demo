"""Real VOPRF proof verification and signed snapshot lookups with captured client wire payloads."""

from __future__ import annotations

import base64
import json
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.utils import timezone

from apps.bank_adapter.signing import sign_object
from apps.intel_client.oprf import OprfServer
from apps.intel_client.service import IntelClient, payee_input

IBAN = "GB82WEST12345698765432"


def b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


class Network:
    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.server = OprfServer(bytes(range(32)), b"synthetic-test-epoch")
        self.traffic: list[dict[str, Any]] = []
        self.damage = ""
        self.seq = 1
        self.reporters = 2

    def request(self, method: str, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.traffic.append({"method": method, "target": target, "payload": payload})
        now = timezone.now()
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        if target == "/v1/epochs/current":
            return {
                "epoch": 1,
                "oprf_public_key": b64(self.server.public_key),
                "starts_at": (now - timedelta(days=1)).strftime(fmt),
                "ends_at": (now + timedelta(days=1)).strftime(fmt),
            }
        if target == "/v1/snapshots/1":
            tag = self.server.evaluate(payee_input("GB", IBAN))[:16].hex()
            snapshot = sign_object(
                {
                    "epoch": 1,
                    "seq": self.seq,
                    "issued_at": (now - timedelta(seconds=1)).strftime(fmt),
                    "valid_until": (now + timedelta(minutes=9)).strftime(fmt),
                    "suite": "ristretto255-SHA512",
                    "mode": "VOPRF",
                    "oprf_public_key": b64(self.server.public_key),
                    "tag_bytes": 16,
                    "entries": [
                        {
                            "tag": tag,
                            "distinct_reporters": self.reporters,
                            "expires_at": (now + timedelta(days=1)).strftime(fmt),
                        }
                    ],
                    "key_id": "intel:snapshot:1",
                },
                self.key,
            )
            if self.damage == "signature":
                snapshot["seq"] += 1
            elif self.damage == "expired":
                snapshot["valid_until"] = (now - timedelta(seconds=1)).strftime(fmt)
                snapshot = sign_object(snapshot, self.key)
            return snapshot
        assert target == "/v1/oprf/evaluate"
        assert payload is not None
        result = self.server.blind_evaluate([base64.b64decode(value) for value in payload["blinded"]])
        return {
            "epoch": 1,
            "evaluated": [b64(value) for value in result.evaluated],
            "proof": b64(bytes(64) if self.damage == "proof" else result.proof),
        }


def test_blind_hit_and_miss_never_send_identifier_or_tag() -> None:
    network = Network()
    client = IntelClient(network, network.key.public_key())  # type: ignore[arg-type]
    hit = client.lookup(IBAN)
    assert (hit.status, hit.reporters, hit.epoch) == ("HIT", 2, 1)
    assert client.lookup("GB33BUKB20201555555555").status == "MISS"
    traffic = json.dumps(network.traffic)
    assert IBAN not in traffic
    assert "GB33BUKB20201555555555" not in traffic
    tag = network.server.evaluate(payee_input("GB", IBAN))[:16].hex()
    assert tag not in traffic
    evaluations = [row["payload"] for row in network.traffic if row["method"] == "POST"]
    assert all(set(row) == {"epoch", "blinded"} for row in evaluations)
    client.lookup(IBAN)
    assert evaluations[0]["blinded"] != network.traffic[-1]["payload"]["blinded"]


@pytest.mark.parametrize("damage", ["signature", "expired", "proof"])
def test_untrusted_or_expired_response_fails_closed(damage: str) -> None:
    network = Network()
    network.damage = damage
    client = IntelClient(network, network.key.public_key())  # type: ignore[arg-type]
    assert client.lookup(IBAN).status == "UNAVAILABLE"


def test_snapshot_sequence_rollback_fails_closed() -> None:
    network = Network()
    client = IntelClient(network, network.key.public_key())  # type: ignore[arg-type]
    assert client.lookup(IBAN).status == "HIT"
    network.seq = 0
    assert client.lookup(IBAN).status == "UNAVAILABLE"
