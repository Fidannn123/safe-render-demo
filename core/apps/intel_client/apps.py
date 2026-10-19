from __future__ import annotations

from django.apps import AppConfig


class IntelClientConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.intel_client"
    label = "intel_client"
    verbose_name = "intel_client"
