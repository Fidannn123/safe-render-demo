"""RFC 9457 problem+json responses with the typed codes of spec/errors.yaml (D-019)."""

from __future__ import annotations

import json
from functools import cache
from typing import Any

import yaml
from django.conf import settings
from django.http import HttpResponse

CONTENT_TYPE = "application/problem+json"


@cache
def _errors() -> dict[str, Any]:
    data: dict[str, Any] = yaml.safe_load(
        (settings.SAFEPAY_SPEC_DIR / "errors.yaml").read_text(encoding="utf-8")
    )
    return data


class ProblemError(Exception):
    """Raise anywhere in a request; rendered as problem+json by the API exception handler."""

    def __init__(self, code: str, detail: str | None = None, errors: list[dict[str, Any]] | None = None):
        if code not in _errors()["codes"]:
            raise ValueError(f"unknown problem code {code}")
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
        self.errors = errors


def problem_body(
    code: str,
    detail: str | None = None,
    instance: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    spec = _errors()
    entry = spec["codes"][code]
    body: dict[str, Any] = {
        "type": f"{spec['type_prefix']}{code}",
        "title": entry["title"],
        "status": int(entry["status"]),
        "code": code,
    }
    if detail:
        body["detail"] = detail[:2000]
    if instance:
        body["instance"] = instance[:512]
    if errors is not None:
        body["errors"] = errors
    return body


def problem_response(
    code: str,
    detail: str | None = None,
    instance: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> HttpResponse:
    body = problem_body(code, detail, instance, errors)
    return HttpResponse(json.dumps(body), status=body["status"], content_type=CONTENT_TYPE)
