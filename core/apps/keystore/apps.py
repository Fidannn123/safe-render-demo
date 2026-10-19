from __future__ import annotations

from django.apps import AppConfig


class KeystoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.keystore"
    label = "keystore"
    verbose_name = "keystore"
