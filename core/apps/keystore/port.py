"""KeyStore port: per-tenant Ed25519 signing keys addressed by key_id (pyca/cryptography only).

key_id format: "tenant-<tenant uuid>:<purpose>:<version>" (matches common.json KeyId). Production backs
this port with a KMS/HSM; the dev implementation is file-backed (file_store.FileKeyStore).
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

Purpose = Literal["decision", "audit"]
PURPOSES: tuple[Purpose, ...] = ("decision", "audit")
_KEY_ID = re.compile(
    r"^tenant-(?P<tenant>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r":(?P<purpose>[a-z]+):(?P<version>[1-9][0-9]{0,5})$"
)


class KeyStoreError(Exception):
    """Unknown or malformed key_id, or a key file that cannot be used."""


@dataclass(frozen=True, slots=True)
class KeyId:
    tenant_id: UUID
    purpose: Purpose
    version: int

    def __str__(self) -> str:
        return f"tenant-{self.tenant_id}:{self.purpose}:{self.version}"

    @classmethod
    def parse(cls, key_id: str) -> KeyId:
        m = _KEY_ID.fullmatch(key_id)
        if m is None:
            raise KeyStoreError("malformed key_id")
        purpose = m.group("purpose")
        if purpose == "decision":
            return cls(UUID(m.group("tenant")), "decision", int(m.group("version")))
        if purpose == "audit":
            return cls(UUID(m.group("tenant")), "audit", int(m.group("version")))
        raise KeyStoreError("unknown key purpose")


class KeyStore(Protocol):
    def active_key_id(self, tenant_id: UUID, purpose: Purpose) -> str:
        """Current signing key for (tenant, purpose); created on first use."""
        ...

    def rotate(self, tenant_id: UUID, purpose: Purpose) -> str:
        """Create the next key version and make it active; old versions stay for verification."""
        ...

    def sign(self, key_id: str, message: bytes) -> bytes:
        """Raw 64-byte Ed25519 signature."""
        ...

    def public_key(self, key_id: str) -> Ed25519PublicKey: ...


def b64encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def verify_signature(public_key: Ed25519PublicKey, message: bytes, signature_b64: str) -> bool:
    """Ed25519 verification of a standard-base64 signature; False on any malformed input."""
    try:
        sig = base64.b64decode(signature_b64, validate=True)
    except (binascii.Error, ValueError):
        return False
    if len(sig) != 64:
        return False
    try:
        public_key.verify(sig, message)
    except InvalidSignature:
        return False
    return True
