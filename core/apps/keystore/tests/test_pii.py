"""Envelope encryption: per-tenant data keys wrapped by the KEK (D-047). SYNTHETIC IBANs only."""

from __future__ import annotations

import base64
import os
import uuid

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap

from apps.accounts.secrets import random_key
from apps.keystore import pii

IBAN = "GB82WEST12345698765432"


@pytest.fixture(autouse=True)
def fresh_cache() -> None:
    pii.forget_cached_keys()


def test_round_trip_and_version_prefix() -> None:
    tenant = uuid.uuid4()
    sealed = pii.encrypt(IBAN, tenant)
    assert sealed.startswith("v2:")
    assert IBAN not in sealed
    assert pii.decrypt(sealed, tenant) == IBAN


def test_each_tenant_has_its_own_wrapped_data_key() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    pii.encrypt(IBAN, a)
    pii.encrypt(IBAN, b)
    assert pii.tenant_data_key(a) != pii.tenant_data_key(b)
    wrapped = pii._dek_path(a).read_bytes()
    assert len(wrapped) == 40  # RFC 3394 wrap of a 32-byte key
    assert pii.tenant_data_key(a) not in wrapped  # stored only wrapped


def test_ciphertext_moved_to_another_tenant_fails() -> None:
    sealed = pii.encrypt(IBAN, uuid.uuid4())
    with pytest.raises(InvalidTag):
        pii.decrypt(sealed, uuid.uuid4())


def test_tampered_ciphertext_fails() -> None:
    tenant = uuid.uuid4()
    raw = bytearray(base64.b64decode(pii.encrypt(IBAN, tenant)[3:]))
    raw[-1] ^= 1
    with pytest.raises(InvalidTag):
        pii.decrypt("v2:" + base64.b64encode(bytes(raw)).decode(), tenant)


def test_deleting_a_wrapped_key_shreds_only_that_tenant() -> None:
    a, b = uuid.uuid4(), uuid.uuid4()
    sealed_a, sealed_b = pii.encrypt(IBAN, a), pii.encrypt(IBAN, b)
    pii._dek_path(a).unlink()
    pii.forget_cached_keys()
    with pytest.raises(InvalidTag):
        pii.decrypt(sealed_a, a)  # a new key is created; the old ciphertext is unreadable
    assert pii.decrypt(sealed_b, b) == IBAN


def test_wrapped_key_needs_the_kek() -> None:
    tenant = uuid.uuid4()
    pii.encrypt(IBAN, tenant)
    from cryptography.hazmat.primitives.keywrap import aes_key_unwrap  # noqa: PLC0415

    with pytest.raises(InvalidUnwrap):
        aes_key_unwrap(os.urandom(32), pii._dek_path(tenant).read_bytes())


def test_legacy_v1_ciphertext_is_still_readable() -> None:
    tenant = uuid.uuid4()
    nonce = os.urandom(12)
    legacy = AESGCM(random_key("pii_aes256_key")).encrypt(nonce, IBAN.encode(), str(tenant).encode())
    assert pii.decrypt(base64.b64encode(nonce + legacy).decode(), tenant) == IBAN
