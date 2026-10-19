"""Key loading for the SIMULATED bank: attestation signing key and trusted caller public keys.

Paths come from settings (SAFEPAY_KEYS_DIR/ed25519/... by default), so tests can point them at
throwaway keys. Loaded keys are cached per path.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings

from safepay_bank.signing import load_private_key_pem, load_public_key_b64_file


@cache
def _private(path: str) -> Ed25519PrivateKey:
    return load_private_key_pem(Path(path))


@cache
def _public(path: str) -> Ed25519PublicKey:
    return load_public_key_b64_file(Path(path))


def attestation_key() -> tuple[str, Ed25519PrivateKey]:
    """(key_id, private key) used to sign receipts and statements."""
    return settings.SAFEPAY_BANK_ATTESTATION_KEY_ID, _private(str(settings.SAFEPAY_BANK_ATTESTATION_KEY_FILE))


def caller_public_key(key_id: str) -> Ed25519PublicKey | None:
    """Public key of a trusted caller (core service key), or None for an unknown key id."""
    path = settings.SAFEPAY_REQUEST_SIGNING_KEYS.get(key_id)
    if path is None:
        return None
    return _public(str(path))
