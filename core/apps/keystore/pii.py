"""Envelope encryption of personal data: one AES-256 data key per tenant, wrapped by a deployment KEK.

Thin wrapper over pyca/cryptography only (CLAUDE.md "No custom cryptography"): AES-256-GCM for the data,
AES key wrap (RFC 3394) for each tenant's data key. Layout (dev and tests; production maps the KEK to a
KMS/HSM, DECISIONS.md D-018 and D-047):

  <SAFEPAY_KEYSTORE_DIR>/../runtime-secrets/pii_kek        32 random bytes, the key-encryption key
  <SAFEPAY_KEYSTORE_DIR>/../tenant-deks/<tenant uuid>.v1   the tenant's data key, AES-key-wrapped

Ciphertext text form: "v2:" + base64(nonce12 | ciphertext | tag16) with associated data
"safepay/pii/v2|<tenant uuid>", so a ciphertext moved to another tenant fails to decrypt, and deleting
one tenant's wrapped key makes only that tenant's data unreadable (crypto-shredding).
Values without the prefix are the v1 format (one deployment key, tenant id as associated data); they
are still read so databases created before D-047 keep working, and every new write uses v2.
"""

from __future__ import annotations

import base64
import os
import threading
import uuid
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap, aes_key_wrap
from django.conf import settings

from apps.accounts.secrets import random_key

PREFIX = "v2:"
DEK_VERSION = 1
_lock = threading.Lock()
_cache: dict[uuid.UUID, bytes] = {}


def _dek_path(tenant_id: uuid.UUID) -> Path:
    root = Path(settings.SAFEPAY_KEYSTORE_DIR).parent / "tenant-deks"
    return root / f"{uuid.UUID(str(tenant_id))}.v{DEK_VERSION}"


def _aad(tenant_id: uuid.UUID) -> bytes:
    return f"safepay/pii/v2|{uuid.UUID(str(tenant_id))}".encode("ascii")


def tenant_data_key(tenant_id: uuid.UUID) -> bytes:
    """The tenant's data key, created (and wrapped under the KEK) on first use."""
    tenant_id = uuid.UUID(str(tenant_id))
    with _lock:
        cached = _cache.get(tenant_id)
        if cached is not None:
            return cached
        kek = random_key("pii_kek")
        path = _dek_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            wrapped = aes_key_wrap(kek, AESGCM.generate_key(bit_length=256))
            temporary = path.with_name(f".{uuid.uuid4().hex}.tmp")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as output:
                output.write(wrapped)
            try:
                os.link(temporary, path)  # first writer wins; web and worker share this directory
            except FileExistsError:
                pass
            finally:
                temporary.unlink()
        dek = aes_key_unwrap(kek, path.read_bytes())
        _cache[tenant_id] = dek
        return dek


def encrypt(plaintext: str, tenant_id: uuid.UUID) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(tenant_data_key(tenant_id)).encrypt(nonce, plaintext.encode("utf-8"), _aad(tenant_id))
    return PREFIX + base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(value: str, tenant_id: uuid.UUID) -> str:
    if value.startswith(PREFIX):
        data = base64.b64decode(value[len(PREFIX) :], validate=True)
        key, aad = tenant_data_key(tenant_id), _aad(tenant_id)
    else:  # v1: one deployment key, tenant id as associated data
        data = base64.b64decode(value, validate=True)
        key, aad = random_key("pii_aes256_key"), str(tenant_id).encode()
    return AESGCM(key).decrypt(data[:12], data[12:], aad).decode("utf-8")


def forget_cached_keys() -> None:
    """Drop unwrapped data keys held in memory (tests; key rotation)."""
    with _lock:
        _cache.clear()
