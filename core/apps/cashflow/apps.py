from __future__ import annotations

from django.apps import AppConfig


class CashflowConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.cashflow"
    label = "cashflow"
    verbose_name = "cashflow"
