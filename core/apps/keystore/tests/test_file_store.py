"""File-backed KeyStore: per-tenant keys, key_id format, rotation, path safety. Uses tmp_path only."""

from __future__ import annotations

import re
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from apps.keystore.file_store import FileKeyStore
from apps.keystore.port import KeyId, KeyStoreError, b64encode, verify_signature

TENANT_A = uuid.UUID("11111111-2222-4333-8444-555555555555")
TENANT_B = uuid.UUID("22222222-3333-4444-8555-666666666666")
KEY_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")  # common.json KeyId


@pytest.fixture
def store(tmp_path: Path) -> FileKeyStore:
    return FileKeyStore(tmp_path)


def test_first_use_creates_v1_and_is_stable(store: FileKeyStore, tmp_path: Path) -> None:
    key_id = store.active_key_id(TENANT_A, "decision")
    assert key_id == f"tenant-{TENANT_A}:decision:1"
    assert KEY_ID_PATTERN.fullmatch(key_id)
    assert store.active_key_id(TENANT_A, "decision") == key_id
    reopened = FileKeyStore(tmp_path)
    assert reopened.active_key_id(TENANT_A, "decision") == key_id
    sig = store.sign(key_id, b"msg")
    assert verify_signature(reopened.public_key(key_id), b"msg", b64encode(sig))


def test_keys_are_per_tenant_and_purpose(store: FileKeyStore) -> None:
    a = store.active_key_id(TENANT_A, "decision")
    b = store.active_key_id(TENANT_B, "decision")
    audit = store.active_key_id(TENANT_A, "audit")
    raw = {store.public_key(k).public_bytes_raw() for k in (a, b, audit)}
    assert len(raw) == 3
    sig = b64encode(store.sign(a, b"m"))
    assert not verify_signature(store.public_key(b), b"m", sig)
    assert not verify_signature(store.public_key(audit), b"m", sig)


def test_rotation_keeps_old_keys_verifiable(store: FileKeyStore) -> None:
    v1 = store.active_key_id(TENANT_A, "decision")
    old_sig = b64encode(store.sign(v1, b"m"))
    v2 = store.rotate(TENANT_A, "decision")
    assert v2.endswith(":decision:2")
    assert store.active_key_id(TENANT_A, "decision") == v2
    assert verify_signature(store.public_key(v1), b"m", old_sig)
    assert not verify_signature(store.public_key(v2), b"m", old_sig)


def test_signature_is_raw_ed25519(store: FileKeyStore) -> None:
    key_id = store.active_key_id(TENANT_A, "decision")
    sig = store.sign(key_id, b"m")
    assert len(sig) == 64
    assert re.fullmatch(r"[A-Za-z0-9+/]{86}==", b64encode(sig))


@pytest.mark.parametrize(
    "key_id",
    [
        "tenant-../../etc:decision:1",
        f"tenant-{TENANT_A}:other:1",
        f"tenant-{TENANT_A}:decision:0",
        f"tenant-{TENANT_A}:decision:1/../../x",
        f"tenant-{str(TENANT_A).upper()}:decision:1",
        "",
    ],
)
def test_malformed_key_ids_rejected(store: FileKeyStore, key_id: str) -> None:
    with pytest.raises(KeyStoreError):
        store.sign(key_id, b"m")


def test_unknown_version_rejected(store: FileKeyStore) -> None:
    store.active_key_id(TENANT_A, "decision")
    with pytest.raises(KeyStoreError, match="unknown key_id"):
        store.public_key(f"tenant-{TENANT_A}:decision:9")


def test_key_id_roundtrip() -> None:
    k = KeyId(TENANT_A, "audit", 3)
    assert KeyId.parse(str(k)) == k


def test_verify_signature_rejects_garbage(store: FileKeyStore) -> None:
    pub = store.public_key(store.active_key_id(TENANT_A, "decision"))
    assert not verify_signature(pub, b"m", "%%%")
    assert not verify_signature(pub, b"m", b64encode(b"\x00" * 64))
    assert not verify_signature(pub, b"m", b64encode(b"\x00" * 10))


def test_concurrent_first_use_publishes_complete_key(tmp_path: Path) -> None:
    def sign(_: int) -> bytes:
        independent = FileKeyStore(tmp_path)
        key = independent.active_key_id(TENANT_A, "decision")
        return independent.sign(key, b"concurrent first use")

    with ThreadPoolExecutor(max_workers=8) as pool:
        signatures = list(pool.map(sign, range(32)))
    assert len(set(signatures)) == 1
    assert not list(tmp_path.rglob("*.tmp"))
