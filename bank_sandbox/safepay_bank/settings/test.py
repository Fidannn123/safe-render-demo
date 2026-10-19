"""Test settings: same PostgreSQL 18 as compose (127.0.0.1:55432), fixed non-secret key."""

from __future__ import annotations

from .base import *

SECRET_KEY = "test-only-insecure-key-not-a-secret"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Tests run as the OWNER (creates the test DB, migrates, applies db_policy); role tests connect
# separately as the APP role. SAFEPAY_TEST_DB_SUFFIX isolates parallel worktrees (docs/PARALLEL.md).
_SUFFIX = env("SAFEPAY_TEST_DB_SUFFIX")
if _SUFFIX and not _SUFFIX.isalnum():
    raise ValueError("SAFEPAY_TEST_DB_SUFFIX must be alphanumeric")
DATABASES["default"]["USER"] = env("DB_USER", "safepay_bank_owner")
DATABASES["default"]["PASSWORD"] = secret("DB_PASSWORD", default=dev_secret("bank_owner_db_password"))
DATABASES["default"]["TEST"] = {"NAME": f"test_safepay_bank_{_SUFFIX}" if _SUFFIX else "test_safepay_bank"}
SAFEPAY_DB_APP_PASSWORD = secret("DB_APP_PASSWORD", default=dev_secret("bank_app_db_password"))
DATABASES["default"]["CONN_MAX_AGE"] = 0
# Generous timeout: Docker Desktop port forwarding can be slow while images build.
DATABASES["default"]["OPTIONS"]["connect_timeout"] = 15
