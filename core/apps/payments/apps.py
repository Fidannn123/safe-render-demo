from __future__ import annotations

from django.apps import AppConfig


class PaymentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.payments"
    label = "payments"
    verbose_name = "payments"

    def ready(self) -> None:
        # Load spec/state_machine.yaml at startup and fail fast if a guard, posting_condition or effect
        # named in the spec has no implementation.
        from . import guards  # noqa: F401, PLC0415 (registers the built-in guards)
        from .service import validate_spec_bindings  # noqa: PLC0415

        validate_spec_bindings()
