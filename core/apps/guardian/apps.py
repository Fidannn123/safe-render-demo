from __future__ import annotations

from django.apps import AppConfig


class GuardianConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.guardian"
    label = "guardian"
    verbose_name = "guardian"
