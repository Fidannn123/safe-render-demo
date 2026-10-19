"""File-backed KeyStore for dev and tests (DECISIONS.md D-018: production uses KMS/HSM).

Layout: <root>/<tenant uuid>/<purpose>/v<version>.pem (PKCS#8 PEM, unencrypted, dev only) and
<root>/<tenant uuid>/<purpose>/ACTIVE (text: active version). Paths are built only from a parsed
UUID, an allow-listed purpose and an integer, so a key_id can never escape the root. Key files are
created exclusively (open mode "x") so concurrent first use cannot overwrite a key.
"""

from __future__ import annotations

import contextlib
import os
import threading
import uuid
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .port import PURPOSES, KeyId, KeyStoreError, Purpose


class FileKeyStore:
    def __init__(self, root: Path) -> None:
        self._root = Path(root)
        self._lock = threading.Lock()
        self._cache: dict[str, Ed25519PrivateKey] = {}

    # ------------------------------------------------------------------ paths
    def _dir(self, tenant_id: UUID, purpose: Purpose) -> Path:
        if purpose not in PURPOSES:
            raise KeyStoreError("unknown key purpose")
        return self._root / str(UUID(str(tenant_id))) / purpose

    def _pem_path(self, key: KeyId) -> Path:
        return self._dir(key.tenant_id, key.purpose) / f"v{key.version}.pem"

    def _active_version(self, tenant_id: UUID, purpose: Purpose) -> int | None:
        marker = self._dir(tenant_id, purpose) / "ACTIVE"
        if not marker.is_file():
            return None
        text = marker.read_text(encoding="ascii").strip()
        if not text.isdigit():
            raise KeyStoreError("corrupt ACTIVE marker")
        return int(text)

    # ------------------------------------------------------------------ writes
    def _create(self, key: KeyId) -> None:
        directory = self._dir(key.tenant_id, key.purpose)
        directory.mkdir(parents=True, exist_ok=True)
        pem = Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        path = self._pem_path(key)
        # Publish a complete PEM atomically. Another process must never observe a newly-created
        # final path before its contents have been written (web and worker share this directory).
        temporary = directory / f".{uuid.uuid4().hex}.pem.tmp"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(pem)
                fh.flush()
                os.fsync(fh.fileno())
            os.link(temporary, path)  # atomic no-overwrite publication on the same filesystem
        finally:
            temporary.unlink(missing_ok=True)
        tmp = directory / f"ACTIVE.{os.getpid()}.{threading.get_ident()}.tmp"
        tmp.write_text(str(key.version), encoding="ascii")
        tmp.replace(directory / "ACTIVE")

    def active_key_id(self, tenant_id: UUID, purpose: Purpose) -> str:
        with self._lock:
            version = self._active_version(tenant_id, purpose)
            if version is None:
                key = KeyId(UUID(str(tenant_id)), purpose, 1)
                with contextlib.suppress(FileExistsError):  # another process created v1 first
                    self._create(key)
                return str(key)
            return str(KeyId(UUID(str(tenant_id)), purpose, version))

    def rotate(self, tenant_id: UUID, purpose: Purpose) -> str:
        with self._lock:
            current = self._active_version(tenant_id, purpose) or 0
            key = KeyId(UUID(str(tenant_id)), purpose, current + 1)
            self._create(key)
            return str(key)

    # ------------------------------------------------------------------ reads
    def _private(self, key_id: str) -> Ed25519PrivateKey:
        cached = self._cache.get(key_id)
        if cached is not None:
            return cached
        path = self._pem_path(KeyId.parse(key_id))
        if not path.is_file():
            raise KeyStoreError("unknown key_id")
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(loaded, Ed25519PrivateKey):
            raise KeyStoreError("key file is not Ed25519")
        self._cache[key_id] = loaded
        return loaded

    def sign(self, key_id: str, message: bytes) -> bytes:
        return self._private(key_id).sign(message)

    def public_key(self, key_id: str) -> Ed25519PublicKey:
        return self._private(key_id).public_key()


def default_keystore() -> FileKeyStore:
    """Dev keystore under settings.SAFEPAY_KEYSTORE_DIR."""
    from django.conf import settings  # noqa: PLC0415

    root: Path = settings.SAFEPAY_KEYSTORE_DIR
    return FileKeyStore(root)
