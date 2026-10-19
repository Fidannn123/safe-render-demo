"""Verify a public audit export without a database or private keys. Synthetic prototype only.

Usage: uv run python ops/scripts/verify_audit_export.py <directory> [--trusted-keys <json>]
An independently retained --trusted-keys file pins the verification keys. Without it, the check
proves internal consistency relative to the supplied public keys, not the identity of their owner.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


def verify(directory: Path, trusted_keys: Path | None = None) -> dict[str, int]:
    keys = json.loads((trusted_keys or directory / "audit-public-keys.json").read_text("utf-8"))
    events = [json.loads(line) for line in (directory / "audit.jsonl").read_text("utf-8").splitlines()]
    documents = [
        json.loads(line) for line in (directory / "checkpoints.jsonl").read_text("utf-8").splitlines()
    ]
    previous, heads, tenant = "0" * 64, {}, events[0]["tenant_id"] if events else None
    for seq, event in enumerate(events, 1):
        if event["seq"] != seq or event["tenant_id"] != tenant or event["prev_hash"] != previous:
            raise ValueError("audit sequence, tenant or linkage mismatch")
        body = {key: value for key, value in event.items() if key != "hash"}
        previous = hashlib.sha256(bytes.fromhex(previous) + rfc8785.dumps(body)).hexdigest()
        if event["hash"] != previous:
            raise ValueError("audit hash mismatch")
        heads[seq] = previous
    end, previous_checkpoint = 0, None
    for document in documents:
        start, next_end = document["seq_from"], document["seq_to"]
        if document["tenant_id"] != tenant or not document["key_id"].startswith(f"tenant-{tenant}:audit:"):
            raise ValueError("checkpoint tenant or key-purpose mismatch")
        if start != end + 1 or next_end < start or document["event_count"] != next_end - start + 1:
            raise ValueError("checkpoint range mismatch")
        if (
            heads.get(next_end) != document["head_hash"]
            or document["prev_checkpoint_hash"] != previous_checkpoint
        ):
            raise ValueError("checkpoint head or linkage mismatch")
        public = serialization.load_pem_public_key(keys[document["key_id"]].encode("ascii"))
        if not isinstance(public, Ed25519PublicKey):
            raise ValueError("checkpoint key must be Ed25519")
        unsigned = {key: value for key, value in document.items() if key != "sig"}
        public.verify(base64.b64decode(document["sig"], validate=True), rfc8785.dumps(unsigned))
        previous_checkpoint = hashlib.sha256(rfc8785.dumps(document)).hexdigest()
        end = next_end
    if end != len(events):
        raise ValueError("unsigned audit tail")
    return {"events": len(events), "checkpoints": len(documents)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--trusted-keys", type=Path)
    options = parser.parse_args()
    result: dict[str, Any] = {"verified": True, **verify(options.directory, options.trusted_keys)}
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
