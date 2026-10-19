"""Thin wrapper over pyca/cryptography Ed25519 and the rfc8785 library (no custom cryptography).

SIMULATED bank attestation: receipts and statements are signed over the RFC 8785 canonical JSON of
every field except `sig` (spec/schemas/Receipt.json, Statement.json). `key_id` is inside the signed
content. Signatures are standard base64 with padding (common.json#/$defs/Ed25519Signature).
HUMAN REVIEW: crypto wrapper (CLAUDE.md definition of done).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import rfc8785
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

SIG_FIELD = "sig"


def canonical_json(obj: Any) -> bytes:
    """RFC 8785 (JCS) canonical UTF-8 bytes."""
    return rfc8785.dumps(obj)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_private_key_pem(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError(f"{path} is not an Ed25519 private key")
    return key


def public_key_from_b64(value: str) -> Ed25519PublicKey:
    """Raw 32-byte public key, standard base64 (the format of ops/dev-keys/ed25519/*.pub)."""
    raw = base64.b64decode(value.strip(), validate=True)
    return Ed25519PublicKey.from_public_bytes(raw)


def load_public_key_b64_file(path: Path) -> Ed25519PublicKey:
    return public_key_from_b64(path.read_text(encoding="ascii"))


def public_key_b64(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign_bytes(key: Ed25519PrivateKey, message: bytes) -> str:
    return base64.b64encode(key.sign(message)).decode("ascii")


def verify_bytes(key: Ed25519PublicKey, message: bytes, signature_b64: str) -> bool:
    try:
        signature = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(signature) != 64:
        return False
    try:
        key.verify(signature, message)
    except InvalidSignature:
        return False
    return True


def signing_payload(document: Mapping[str, Any]) -> bytes:
    """Canonical bytes of every field except `sig`."""
    return canonical_json({k: v for k, v in document.items() if k != SIG_FIELD})


def sign_document(document: Mapping[str, Any], key: Ed25519PrivateKey, key_id: str) -> dict[str, Any]:
    """Return a copy with `key_id` set and `sig` over the canonical JSON of all other fields."""
    unsigned = {k: v for k, v in document.items() if k != SIG_FIELD}
    unsigned["key_id"] = key_id
    return {**unsigned, SIG_FIELD: sign_bytes(key, canonical_json(unsigned))}


def verify_document(document: Mapping[str, Any], key: Ed25519PublicKey) -> bool:
    sig = document.get(SIG_FIELD)
    if not isinstance(sig, str):
        return False
    return verify_bytes(key, signing_payload(document), sig)
