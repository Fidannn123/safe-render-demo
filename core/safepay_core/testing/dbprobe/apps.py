from __future__ import annotations

from django.apps import AppConfig


class DbProbeConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "safepay_core.testing.dbprobe"
    label = "dbprobe"
