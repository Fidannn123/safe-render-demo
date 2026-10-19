"""Crypto wrapper tests: RFC 8032 vectors through the wrapper, RFC 8785 canonicalisation, tamper detection."""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

from safepay_bank.reqsig import canonical_request, sign_request, verify_request
from safepay_bank.signing import (
    canonical_json,
    load_private_key_pem,
    load_public_key_b64_file,
    public_key_b64,
    sign_bytes,
    sign_document,
    verify_bytes,
    verify_document,
)


def _rfc8032() -> list[dict[str, str]]:
    doc = json.loads((settings.SAFEPAY_SPEC_DIR / "vectors" / "ed25519" / "rfc8032.json").read_text("utf-8"))
    vectors: list[dict[str, str]] = doc["vectors"]
    return vectors


@pytest.mark.parametrize("vec", _rfc8032(), ids=lambda v: v["name"])
def test_wrapper_matches_rfc8032(vec: dict[str, str]) -> None:
    key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(vec["secret_key"]))
    message = bytes.fromhex(vec["message"])
    sig = sign_bytes(key, message)
    assert base64.b64decode(sig).hex() == vec["signature"]
    public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(vec["public_key"]))
    assert verify_bytes(public, message, sig)
    assert not verify_bytes(public, message + b"x", sig)


def test_verify_rejects_garbage_signatures() -> None:
    public = Ed25519PrivateKey.generate().public_key()
    assert not verify_bytes(public, b"m", "not base64!")
    assert not verify_bytes(public, b"m", base64.b64encode(b"short").decode())


def test_canonical_json_is_rfc8785() -> None:
    assert canonical_json({"b": 1, "a": [True, None, "é"]}) == '{"a":[true,null,"é"],"b":1}'.encode()


def test_document_signature_excludes_sig_and_covers_key_id() -> None:
    key = Ed25519PrivateKey.generate()
    doc = sign_document({"z": 1, "a": "x", "sig": "ignored"}, key, "bank:attestation:t")
    assert doc["key_id"] == "bank:attestation:t"
    assert verify_document(doc, key.public_key())
    reordered = dict(reversed(list(doc.items())))
    assert verify_document(reordered, key.public_key())
    for field, value in (("z", 2), ("key_id", "bank:attestation:other"), ("a", "y")):
        assert not verify_document({**doc, field: value}, key.public_key())
    assert not verify_document({k: v for k, v in doc.items() if k != "sig"}, key.public_key())
    assert not verify_document(doc, Ed25519PrivateKey.generate().public_key())


def test_request_signature_binds_every_component() -> None:
    key = Ed25519PrivateKey.generate()
    h = sign_request(
        key,
        "core:service:1",
        method="POST",
        path="/v1/transfers",
        query="",
        body=b'{"a":1}',
        timestamp=1_760_000_000,
        nonce="ab" * 16,
    )
    args: dict[str, Any] = {
        "method": "POST",
        "path": "/v1/transfers",
        "query": "",
        "timestamp": h.timestamp,
        "nonce": h.nonce,
        "body": b'{"a":1}',
    }
    assert verify_request(key.public_key(), signature_b64=h.signature, **args)
    for field, value in (
        ("method", "GET"),
        ("path", "/v1/transfer"),
        ("query", "x=1"),
        ("timestamp", "1760000001"),
        ("nonce", "cd" * 16),
        ("body", b'{"a":2}'),
    ):
        assert not verify_request(key.public_key(), signature_b64=h.signature, **{**args, field: value})


def test_canonical_request_format() -> None:
    raw = canonical_request(
        "get", "/v1/statements", "account_id=x&date=2026-10-19", timestamp="1760000000", nonce="ab", body=b""
    )
    assert raw == (
        b"GET\n/v1/statements?account_id=x&date=2026-10-19\n1760000000\nab\n"
        b"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_shared_dev_keys_load_if_present() -> None:
    """The real dev keys (SAFEPAY_KEYS_DIR, read only) have the formats the services expect."""
    ed = Path(settings.SAFEPAY_KEYS_DIR) / "ed25519"
    if not (ed / "bank_attestation.pem").is_file():
        pytest.skip("dev keys not generated")
    private = load_private_key_pem(ed / "bank_attestation.pem")
    public = load_public_key_b64_file(ed / "bank_attestation.pub")
    assert public_key_b64(private.public_key()) == public_key_b64(public)
    load_public_key_b64_file(ed / "core_service.pub")
