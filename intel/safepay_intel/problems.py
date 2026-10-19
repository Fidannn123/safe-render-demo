"""RFC 9457 problem+json responses with the typed codes of spec/errors.yaml."""

from __future__ import annotations

import json
from functools import cache
from typing import Any

import yaml
from django.conf import settings
from django.http import HttpRequest, HttpResponse

PROBLEM_CONTENT_TYPE = "application/problem+json"


@cache
def error_codes() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load((settings.SAFEPAY_SPEC_DIR / "errors.yaml").read_text(encoding="utf-8"))
    codes: dict[str, dict[str, Any]] = data["codes"]
    return codes


class ProblemError(Exception):
    """Raise anywhere in a request to return the typed problem response for `code`."""

    def __init__(self, code: str, detail: str = "", errors: list[dict[str, Any]] | None = None) -> None:
        if code not in error_codes():
            raise ValueError(f"unknown problem code {code}")
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
        self.errors = errors


def problem_response(
    request: HttpRequest, code: str, detail: str = "", errors: list[dict[str, Any]] | None = None
) -> HttpResponse:
    meta = error_codes()[code]
    body: dict[str, Any] = {
        "type": f"urn:safepay:problem:{code}",
        "title": meta["title"],
        "status": int(meta["status"]),
        "code": code,
        "instance": request.path[:512],
    }
    if detail:
        body["detail"] = detail[:2000]
    if errors:
        body["errors"] = errors
    return HttpResponse(json.dumps(body), status=int(meta["status"]), content_type=PROBLEM_CONTENT_TYPE)


def validation_errors(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ninja/pydantic error list -> Problem.errors items ({loc, msg})."""
    out: list[dict[str, Any]] = []
    for err in raw[:50]:
        loc = [p if isinstance(p, int) else str(p) for p in err.get("loc", [])]
        out.append({"loc": loc, "msg": str(err.get("msg", "invalid"))[:500]})
    return out
