"""G0 boot tests for core: health endpoint, Argon2 configuration, app registry."""

from __future__ import annotations

import json
from typing import Any
from unittest import mock

import pytest
import yaml
from django.apps import apps
from django.conf import settings
from django.contrib.auth.hashers import check_password, make_password
from django.db import OperationalError
from django.test import Client
from jsonschema import Draft202012Validator

CORE_APPS = {
    "accounts",
    "tenants",
    "vendors",
    "invoices",
    "agent",
    "guardian",
    "payments",
    "ledger",
    "cashflow",
    "approvals",
    "audit",
    "intel_client",
    "bank_adapter",
    "recon",
    "evidence",
    "keystore",
}


def health_schema() -> dict[str, Any]:
    spec = yaml.safe_load((settings.SAFEPAY_SPEC_DIR / "openapi-core.yaml").read_text(encoding="utf-8"))
    schema: dict[str, Any] = spec["components"]["schemas"]["Health"]
    return schema


@pytest.mark.django_db
def test_healthz_ok_and_matches_spec(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {"status": "ok", "service": "core", "db": "ok"}
    Draft202012Validator(health_schema()).validate(body)


def test_healthz_degraded_when_db_down(client: Client) -> None:
    with mock.patch("safepay_core.health.connection.ensure_connection", side_effect=OperationalError("down")):
        response = client.get("/healthz")
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body == {"status": "degraded", "service": "core", "db": "unavailable"}
    Draft202012Validator(health_schema()).validate(body)


def test_healthz_rejects_post(client: Client) -> None:
    assert client.post("/healthz").status_code == 405


def test_argon2_is_first_and_pbkdf2_only_second() -> None:
    assert settings.PASSWORD_HASHERS == [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    ]


def test_new_passwords_use_argon2id() -> None:
    encoded = make_password("correct horse battery staple")
    assert encoded.startswith("argon2$argon2id$")
    assert check_password("correct horse battery staple", encoded)


def test_all_core_apps_installed() -> None:
    labels = {cfg.label for cfg in apps.get_app_configs() if cfg.name.startswith("apps.")}
    assert labels == CORE_APPS


def test_custom_user_model_uses_uuid() -> None:
    assert settings.AUTH_USER_MODEL == "accounts.User"
    assert apps.get_model("accounts", "User")._meta.pk.get_internal_type() == "UUIDField"


def test_llm_defaults_to_mock_in_tests() -> None:
    assert settings.SAFEPAY_LLM == "mock"


def test_session_cookies_hardened() -> None:
    assert settings.SESSION_COOKIE_SECURE
    assert settings.SESSION_COOKIE_HTTPONLY
    assert settings.SESSION_COOKIE_SAMESITE == "Lax"
    assert settings.CSRF_COOKIE_SECURE


def test_wsgi_application_loads() -> None:
    """Catches broken entry points before the container does (gunicorn imports this)."""
    from safepay_core.wsgi import application  # noqa: PLC0415

    assert callable(application)


def test_no_cdn_docs_page_but_openapi_served(client: Client) -> None:
    assert client.get("/api/v1/docs").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 200
