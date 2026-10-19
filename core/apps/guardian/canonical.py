"""RFC 8785 canonical JSON (rfc8785 library) and SHA-256 helpers. No custom serialisation rules."""

from __future__ import annotations

import hashlib
from typing import Any

import rfc8785
from pydantic import BaseModel


def canonical_bytes(value: Any) -> bytes:
    """RFC 8785 (JCS) bytes of a JSON value or a pydantic model (dumped in JSON mode)."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return rfc8785.dumps(value)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
