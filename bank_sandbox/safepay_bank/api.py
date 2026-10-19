"""django-ninja API roots: /v1 (accounts, transfers, statements) and /admin (SIMULATION ONLY).

Every error is RFC 9457 problem+json with a typed code from spec/errors.yaml. Authentication is the
request-signature middleware (safepay_bank.middleware), which runs before these views.
"""

from __future__ import annotations

import logging
from typing import Any

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError, ValidationError

from apps.accounts.api import router as accounts_router
from apps.statements.api import router as statements_router
from apps.transfers.api import admin_router
from apps.transfers.api import router as transfers_router
from safepay_bank.problems import ProblemError, problem_response

log = logging.getLogger("safepay_bank.api")

STATUS_CODES = {401: "SIGNATURE_INVALID", 404: "NOT_FOUND", 422: "VALIDATION_FAILED", 429: "RATE_LIMITED"}


def _loc(loc: Any) -> list[str | int]:
    return [p if isinstance(p, int) else str(p) for p in (loc or [])]


def install_problem_handlers(target: NinjaAPI) -> None:
    @target.exception_handler(ProblemError)
    def _problem(request: HttpRequest, exc: ProblemError) -> HttpResponse:
        return problem_response(exc.code, exc.detail, request.path, exc.errors)

    @target.exception_handler(ValidationError)
    def _validation(request: HttpRequest, exc: ValidationError) -> HttpResponse:
        errors = [{"loc": _loc(e.get("loc")), "msg": str(e.get("msg", "invalid"))[:500]} for e in exc.errors]
        return problem_response("VALIDATION_FAILED", "request failed validation", request.path, errors)

    @target.exception_handler(AuthenticationError)
    def _auth(request: HttpRequest, exc: AuthenticationError) -> HttpResponse:
        return problem_response("SIGNATURE_INVALID", None, request.path)

    @target.exception_handler(HttpError)
    def _http(request: HttpRequest, exc: HttpError) -> HttpResponse:
        code = STATUS_CODES.get(
            exc.status_code, "INTERNAL" if exc.status_code >= 500 else "VALIDATION_FAILED"
        )
        return problem_response(code, str(exc)[:2000], request.path)

    @target.exception_handler(Exception)
    def _internal(request: HttpRequest, exc: Exception) -> HttpResponse:
        log.exception("unhandled error on %s", request.path)
        return problem_response("INTERNAL", None, request.path)


api = NinjaAPI(
    title="SafePay bank sandbox API (SIMULATED)",
    version="1.0.0",
    urls_namespace="bank-api",
    description="Implements spec/openapi-bank.yaml. SIMULATED bank: no real accounts, no real money.",
    docs_url=None,  # default Swagger UI loads assets from a CDN (unreachable for some reviewers)
)
api.add_router("", accounts_router)
api.add_router("", transfers_router)
api.add_router("", statements_router)
install_problem_handlers(api)

admin_api = NinjaAPI(
    title="SafePay bank sandbox admin API (SIMULATION ONLY)",
    version="1.0.0",
    urls_namespace="bank-admin",
    description="Failure injection for chaos tests and the demo. SIMULATION ONLY.",
    docs_url=None,
    openapi_url=None,
)
admin_api.add_router("", admin_router)
install_problem_handlers(admin_api)
