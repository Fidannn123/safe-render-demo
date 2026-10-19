"""Project AppConfig: applies the database policy after every migrate (DECISIONS.md D-033)."""

from __future__ import annotations

from typing import Any

from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _apply_db_policy(sender: AppConfig, using: str = "default", **kwargs: Any) -> None:
    from safepay_core.db_policy import apply_db_policy  # noqa: PLC0415 (app registry must be ready)

    apply_db_policy(using)


class ProjectConfig(AppConfig):
    name = "safepay_core"
    label = "safepay_core"
    verbose_name = "SafePay core project"

    def ready(self) -> None:
        post_migrate.connect(_apply_db_policy, sender=self, dispatch_uid="safepay_core_db_policy")
