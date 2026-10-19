"""Thin wrappers over pyca/cryptography (Ed25519, SHA-256) and rfc8785 (JCS). No custom cryptography.

HUMAN REVIEW REQUIRED (crypto wrapper). Conventions shared with core and bank (spec/):
  * signed JSON objects: Ed25519 over RFC 8785 canonical JSON of every field except `sig`;
  * signatures and raw public keys: standard base64 with padding;
  * request signing (DECISIONS.md D-012): Ed25519 over
    METHOD "\\n" PATH["?" QUERY] "\\n" TIMESTAMP "\\n" NONCE "\\n" hex(SHA-256(body)).
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

ED25519_PUBLIC_BYTES = 32
ED25519_SIGNATURE_BYTES = 64


class KeyFormatError(ValueError):
    """A key file or encoded key is malformed."""


def canonical_json(obj: Any) -> bytes:
    """RFC 8785 (JCS) canonical serialization via the rfc8785 library."""
    return rfc8785.dumps(obj)


def unsigned_canonical(obj: Mapping[str, Any]) -> bytes:
    """Canonical bytes of a signed object: every field except `sig`."""
    return canonical_json({k: v for k, v in obj.items() if k != "sig"})


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64decode_strict(text: str) -> bytes:
    """Standard base64 with padding; rejects any non-alphabet character."""
    try:
        return base64.b64decode(text.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise KeyFormatError("invalid base64") from exc


def public_key_from_raw(raw: bytes) -> Ed25519PublicKey:
    if len(raw) != ED25519_PUBLIC_BYTES:
        raise KeyFormatError("Ed25519 public key must be 32 bytes")
    return Ed25519PublicKey.from_public_bytes(raw)


def load_public_key_b64_file(path: Path) -> Ed25519PublicKey:
    """ops/dev-keys/ed25519/<name>.pub: raw 32-byte public key, base64."""
    return public_key_from_raw(b64decode_strict(path.read_text(encoding="ascii").strip()))


def load_private_key_pem_file(path: Path) -> Ed25519PrivateKey:
    """ops/dev-keys/ed25519/<name>.pem: PKCS#8 PEM, unencrypted (dev/demo; KMS/HSM in production)."""
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise KeyFormatError(f"{path.name} is not an Ed25519 private key")
    return key


def raw_public_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def sign_b64(key: Ed25519PrivateKey, message: bytes) -> str:
    return b64encode(key.sign(message))


def verify_b64(key: Ed25519PublicKey, message: bytes, signature_b64: str) -> bool:
    """True only for a well-formed 64-byte signature that verifies over message."""
    try:
        sig = b64decode_strict(signature_b64)
    except KeyFormatError:
        return False
    if len(sig) != ED25519_SIGNATURE_BYTES:
        return False
    try:
        key.verify(sig, message)
    except InvalidSignature:
        return False
    return True


def sign_object(key: Ed25519PrivateKey, obj: Mapping[str, Any]) -> str:
    return sign_b64(key, unsigned_canonical(obj))


def verify_object(key: Ed25519PublicKey, obj: Mapping[str, Any]) -> bool:
    sig = obj.get("sig")
    if not isinstance(sig, str):
        return False
    try:
        message = unsigned_canonical(obj)
    except (TypeError, ValueError, rfc8785.CanonicalizationError):
        return False
    return verify_b64(key, message, sig)


def request_signing_string(method: str, path_qs: str, timestamp: str, nonce: str, body: bytes) -> bytes:
    """D-012 canonical request string."""
    return "\n".join([method.upper(), path_qs, timestamp, nonce, sha256_hex(body)]).encode("utf-8")
