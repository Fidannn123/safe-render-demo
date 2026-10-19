from __future__ import annotations

from django.apps import AppConfig


class ReconConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.recon"
    label = "recon"
    verbose_name = "recon"
