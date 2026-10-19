from __future__ import annotations

from django.apps import AppConfig


class BankAdapterConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bank_adapter"
    label = "bank_adapter"
    verbose_name = "bank_adapter"
