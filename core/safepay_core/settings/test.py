"""Test settings: same PostgreSQL 18 as compose (127.0.0.1:55432), mock LLM, fixed non-secret key."""

from __future__ import annotations

import tempfile
from pathlib import Path

from .base import *

SECRET_KEY = "test-only-insecure-key-not-a-secret"  # noqa: S105
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1"]

# Tests run as the OWNER (creates the test DB, migrates, applies db_policy); role tests connect
# separately as the APP role. SAFEPAY_TEST_DB_SUFFIX isolates parallel worktrees (docs/PARALLEL.md).
_SUFFIX = env("SAFEPAY_TEST_DB_SUFFIX")
if _SUFFIX and not _SUFFIX.isalnum():
    raise ValueError("SAFEPAY_TEST_DB_SUFFIX must be alphanumeric")
DATABASES["default"]["USER"] = env("DB_USER", "safepay_core_owner")
DATABASES["default"]["PASSWORD"] = secret("DB_PASSWORD", default=dev_secret("core_owner_db_password"))
DATABASES["default"]["TEST"] = {"NAME": f"test_safepay_core_{_SUFFIX}" if _SUFFIX else "test_safepay_core"}
SAFEPAY_DB_APP_PASSWORD = secret("DB_APP_PASSWORD", default=dev_secret("core_app_db_password"))
DATABASES["default"]["CONN_MAX_AGE"] = 0

SAFEPAY_LLM = "mock"  # never a real provider in tests

INSTALLED_APPS = [*INSTALLED_APPS, "safepay_core.testing.dbprobe"]
SAFEPAY_APPEND_ONLY_TABLES = (*SAFEPAY_APPEND_ONLY_TABLES, "dbprobe_append_only")
# Generous timeout: Docker Desktop port forwarding can be slow while images build.
DATABASES["default"]["OPTIONS"]["connect_timeout"] = 15

# M2: tests never write into the shared dev-keys directory; keystore tests use tmp_path explicitly.
SAFEPAY_KEYSTORE_DIR = Path(tempfile.gettempdir()) / f"safepay-test-keystore-{_SUFFIX or 'main'}"
