from __future__ import annotations

from pathlib import Path

from django.apps import AppConfig
from django.conf import settings


class OprfConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.oprf"
    label = "oprf"
    verbose_name = "oprf"

    def ready(self) -> None:
        # Fail closed at start-up if the OPRF binding stops matching RFC 9497 (D-048).
        from .selftest import run_self_test  # noqa: PLC0415

        run_self_test(Path(settings.SAFEPAY_SPEC_DIR))
