"""Service request signing wire format (DECISIONS.md D-012, spec/openapi-bank.yaml info.description).

Signed string: METHOD "\\n" PATH ["?" QUERY] "\\n" TIMESTAMP "\\n" NONCE "\\n" hex(SHA-256(body)).
PATH is the raw request path, QUERY the raw query string exactly as sent (omitted with its "?" when
empty). Headers: X-SafePay-Key-Id, X-SafePay-Timestamp (unix seconds, 10 digits), X-SafePay-Nonce
(lower-case hex, 128-256 bits), X-SafePay-Signature (standard base64 Ed25519 signature).
HUMAN REVIEW: crypto wrapper (CLAUDE.md definition of done).
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from safepay_bank.signing import sha256_hex, sign_bytes, verify_bytes

HEADER_KEY_ID = "X-SafePay-Key-Id"
HEADER_TIMESTAMP = "X-SafePay-Timestamp"
HEADER_NONCE = "X-SafePay-Nonce"
HEADER_SIGNATURE = "X-SafePay-Signature"

TIMESTAMP_RE = re.compile(r"^[0-9]{10}$")
NONCE_RE = re.compile(r"^[0-9a-f]{32,64}$")
KEY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")


def canonical_request(
    method: str, path: str, query: str, *, timestamp: str, nonce: str, body: bytes
) -> bytes:
    target = f"{path}?{query}" if query else path
    return "\n".join([method.upper(), target, timestamp, nonce, sha256_hex(body)]).encode("utf-8")


@dataclass(frozen=True)
class SignedHeaders:
    key_id: str
    timestamp: str
    nonce: str
    signature: str

    def as_dict(self) -> dict[str, str]:
        return {
            HEADER_KEY_ID: self.key_id,
            HEADER_TIMESTAMP: self.timestamp,
            HEADER_NONCE: self.nonce,
            HEADER_SIGNATURE: self.signature,
        }


def sign_request(
    key: Ed25519PrivateKey,
    key_id: str,
    *,
    method: str,
    path: str,
    query: str,
    body: bytes,
    timestamp: int,
    nonce: str | None = None,
) -> SignedHeaders:
    """Client side (core bank adapter, tests)."""
    ts = f"{timestamp:010d}"
    nonce_hex = nonce if nonce is not None else secrets.token_hex(16)
    sig = sign_bytes(key, canonical_request(method, path, query, timestamp=ts, nonce=nonce_hex, body=body))
    return SignedHeaders(key_id=key_id, timestamp=ts, nonce=nonce_hex, signature=sig)


def verify_request(
    key: Ed25519PublicKey,
    *,
    method: str,
    path: str,
    query: str,
    timestamp: str,
    nonce: str,
    body: bytes,
    signature_b64: str,
) -> bool:
    return verify_bytes(
        key,
        canonical_request(method, path, query, timestamp=timestamp, nonce=nonce, body=body),
        signature_b64,
    )
