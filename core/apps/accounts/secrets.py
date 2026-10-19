"""Local prototype secrets; production must mount independently managed random keys."""

from __future__ import annotations

import os
from pathlib import Path

from django.conf import settings


def random_key(name: str) -> bytes:
    path = Path(settings.SAFEPAY_KEYSTORE_DIR).parent / "runtime-secrets" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(descriptor, "wb") as output:
        value = os.urandom(32)
        output.write(value)
    return value
