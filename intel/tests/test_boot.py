"""G0 boot tests for intel: health endpoint, Argon2 configuration, app registry."""

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

APPS = {"members", "oprf", "reports", "snapshots"}


def health_schema() -> dict[str, Any]:
    spec = yaml.safe_load((settings.SAFEPAY_SPEC_DIR / "openapi-intel.yaml").read_text(encoding="utf-8"))
    schema: dict[str, Any] = spec["components"]["schemas"]["Health"]
    return schema


@pytest.mark.django_db
def test_healthz_ok_and_matches_spec(client: Client) -> None:
    response = client.get("/healthz")
    assert response.status_code == 200
    body = json.loads(response.content)
    assert body == {"status": "ok", "service": "intel", "db": "ok"}
    Draft202012Validator(health_schema()).validate(body)


def test_healthz_degraded_when_db_down(client: Client) -> None:
    with mock.patch(
        "safepay_intel.health.connection.ensure_connection", side_effect=OperationalError("down")
    ):
        response = client.get("/healthz")
    assert response.status_code == 503
    body = json.loads(response.content)
    assert body == {"status": "degraded", "service": "intel", "db": "unavailable"}
    Draft202012Validator(health_schema()).validate(body)


def test_argon2_is_first_and_pbkdf2_only_second() -> None:
    assert settings.PASSWORD_HASHERS == [
        "django.contrib.auth.hashers.Argon2PasswordHasher",
        "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    ]


def test_new_hashes_use_argon2id() -> None:
    encoded = make_password("correct horse battery staple")
    assert encoded.startswith("argon2$argon2id$")
    assert check_password("correct horse battery staple", encoded)


def test_service_apps_installed() -> None:
    labels = {cfg.label for cfg in apps.get_app_configs() if cfg.name.startswith("apps.")}
    assert labels == APPS


def test_voprf_binding_importable() -> None:
    """M4 wraps this behind apps/oprf/port.py and proves it against spec/vectors (RFC 9497)."""
    import voprf  # noqa: PLC0415

    assert hasattr(voprf, "ristretto")


def test_wsgi_application_loads() -> None:
    """Catches broken entry points before the container does (gunicorn imports this)."""
    from safepay_intel.wsgi import application  # noqa: PLC0415

    assert callable(application)


def test_no_cdn_docs_page_but_openapi_served(client: Client) -> None:
    assert client.get("/v1/docs").status_code == 404
    assert client.get("/v1/openapi.json").status_code == 200
