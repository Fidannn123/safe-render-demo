"""Generate local development secrets and keys into ops/dev-keys/ (gitignored). DEV/DEMO ONLY.

Idempotent: existing files are kept unless --force. Runs on the host (`make keys`) or in the
compose `keygen` one-shot service, so a clean machine needs only Docker.

Layout:
  secrets/<name>            random secrets (DB passwords, Django keys, pepper, KEK, blind-index key)
  ed25519/<name>.pem        PKCS#8 private key      (core request signing, intel snapshot, bank attestation)
  ed25519/<name>.pub        raw 32-byte public key, base64 (pinned by verifiers)
  MANIFEST.json             public fingerprints only (no secret material)

Per-tenant Decision/audit keys and member keys are created at runtime by the KeyStore port (M2/M4);
OPRF epoch keys are created by the intel OPRF port (M4). Production uses KMS/HSM instead (KeyStore port).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
from collections.abc import Callable
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def hex32() -> str:
    return secrets.token_hex(32)


def b64_32() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def django_key() -> str:
    return secrets.token_urlsafe(50)


SECRETS: dict[str, Callable[[], str]] = {
    "postgres_superuser_password": hex32,
    "core_owner_db_password": hex32,  # migrate only
    "core_app_db_password": hex32,  # runtime (web, worker)
    "intel_owner_db_password": hex32,
    "intel_app_db_password": hex32,
    "bank_owner_db_password": hex32,
    "bank_app_db_password": hex32,
    "core_django_secret_key": django_key,
    "intel_django_secret_key": django_key,
    "bank_django_secret_key": django_key,
    "agent_token_pepper": hex32,  # HMAC-SHA256(pepper, token) for agent tokens
    "data_kek": b64_32,  # AES-256-GCM key-encryption key (wraps per-tenant DEKs)
    "blind_index_key": hex32,  # HMAC key for searchable blind index
}
ED25519_KEYS = ["core_service", "intel_snapshot", "bank_attestation"]


def write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    # 0644 so the postgres and app containers (different uids) can read the bind mount. DEV ONLY.
    os.chmod(path, 0o644)


def generate(out: Path, force: bool) -> list[str]:
    created: list[str] = []
    for name, make in SECRETS.items():
        path = out / "secrets" / name
        if force or not path.exists():
            write_private(path, make().encode("ascii"))
            created.append(f"secrets/{name}")
    manifest: dict[str, dict[str, str]] = {}
    for name in ED25519_KEYS:
        priv_path, pub_path = out / "ed25519" / f"{name}.pem", out / "ed25519" / f"{name}.pub"
        if force or not priv_path.exists():
            key = Ed25519PrivateKey.generate()
            pem = key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
            write_private(priv_path, pem)
            raw = key.public_key().public_bytes_raw()
            write_private(pub_path, base64.b64encode(raw) + b"\n")
            created.append(f"ed25519/{name}")
        pub_raw = base64.b64decode(pub_path.read_text(encoding="ascii").strip())
        manifest[name] = {
            "public_key_b64": base64.b64encode(pub_raw).decode(),
            "sha256": hashlib.sha256(pub_raw).hexdigest(),
        }
    (out / "MANIFEST.json").write_text(
        json.dumps({"dev_only": True, "ed25519": manifest}, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return created


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", default=str(Path(__file__).resolve().parents[1] / "dev-keys"))
    parser.add_argument("--force", action="store_true", help="rotate everything (invalidates existing DB!)")
    args = parser.parse_args(argv)
    out = Path(args.out)
    created = generate(out, args.force)
    print(
        f"dev keys in {out}: {len(created)} created, rest kept"
        if created
        else f"dev keys in {out}: up to date"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
