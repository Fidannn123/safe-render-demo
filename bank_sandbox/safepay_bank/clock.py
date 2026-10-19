"""Single source of time for the sandbox (patched in tests)."""

from __future__ import annotations

from datetime import UTC, datetime


def now() -> datetime:
    """Current UTC time, truncated to whole seconds (receipts and lines show RFC 3339 seconds)."""
    return datetime.now(UTC).replace(microsecond=0)


def rfc3339(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
