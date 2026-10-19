"""Settings for the evidence harness (make evidence). Never serves HTTP; requests run in-process.

The harness creates a fresh database (safepay_core_evidence_<seed>) as the OWNER role, migrates it
(post_migrate applies the database policy), then switches to the least-privilege APP role for the run.
The live database is never touched. Mock LLM only. All data is SYNTHETIC; the bank is SIMULATED.
"""

from __future__ import annotations

from .base import *

SECRET_KEY = secret("DJANGO_SECRET_KEY", default="evidence-harness-only-never-serves-http")
ALLOWED_HOSTS = ["testserver"]
SAFEPAY_LLM = "mock"

DATABASES["default"]["USER"] = env("DB_OWNER_USER", "safepay_core_owner")
DATABASES["default"]["PASSWORD"] = secret("DB_OWNER_PASSWORD", default=dev_secret("core_owner_db_password"))
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"]["TEST"] = {"NAME": env("SAFEPAY_EVIDENCE_DB", "safepay_core_evidence")}
SAFEPAY_DB_APP_PASSWORD = secret("DB_APP_PASSWORD", default=dev_secret("core_app_db_password"))
