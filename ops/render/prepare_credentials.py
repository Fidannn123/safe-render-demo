"""Create private Render demo variables and an authenticator setup file (never commit them)."""

from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path


def main() -> None:
    out = Path(__file__).resolve().parents[1] / "dev-keys"
    out.mkdir(parents=True, exist_ok=True)
    variables = out / "render.env"
    if variables.exists():
        raise SystemExit(f"Already prepared: {variables}; existing credentials were kept")
    password = secrets.token_urlsafe(32)
    key = secrets.token_hex(20)
    secret = base64.b32encode(bytes.fromhex(key)).decode("ascii").rstrip("=")
    fd = os.open(variables, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write(f"SAFEPAY_DEMO_PASSWORD={password}\nSAFEPAY_DEMO_TOTP_KEY={key}\n")
    setup = out / "render-authenticator.txt"
    fd = os.open(setup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
        stream.write("Username: logistics.approver\n")
        stream.write(f"Password: {password}\nAuthenticator setup key (Base32): {secret}\n")
        stream.write("Authenticator: TOTP, SHA1, 6 digits, 30 seconds\n")
    print(f"Private deployment variables: {variables}")
    print(f"Private login and authenticator setup: {setup}")


if __name__ == "__main__":
    main()
