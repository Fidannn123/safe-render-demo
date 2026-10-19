"""django-ninja API root for /v1 (contract: spec/openapi-intel.yaml). Every error is RFC 9457 problem+json."""

from __future__ import annotations

import logging

from django.http import HttpRequest, HttpResponse
from ninja import NinjaAPI
from ninja.errors import AuthenticationError, HttpError, ValidationError

from apps.oprf.api import router as oprf_router
from apps.reports.api import router as reports_router
from apps.snapshots.api import router as snapshots_router
from safepay_intel.problems import ProblemError, problem_response, validation_errors

log = logging.getLogger(__name__)

api = NinjaAPI(
    title="SafePay intel network API",
    version="1.0.0",
    urls_namespace="intel-api",
    description="Implements spec/openapi-intel.yaml.",
    docs_url=None,  # default Swagger UI loads assets from a CDN (unreachable for some reviewers)
)


@api.exception_handler(ProblemError)
def _problem(request: HttpRequest, exc: ProblemError) -> HttpResponse:
    return problem_response(request, exc.code, exc.detail, exc.errors)


@api.exception_handler(ValidationError)
def _validation(request: HttpRequest, exc: ValidationError) -> HttpResponse:
    return problem_response(request, "VALIDATION_FAILED", errors=validation_errors(list(exc.errors)))


@api.exception_handler(AuthenticationError)
def _unauthenticated(request: HttpRequest, exc: AuthenticationError) -> HttpResponse:
    return problem_response(request, "SIGNATURE_INVALID", "missing or malformed signing headers")


@api.exception_handler(HttpError)
def _http_error(request: HttpRequest, exc: HttpError) -> HttpResponse:
    code = {404: "NOT_FOUND", 429: "RATE_LIMITED"}.get(exc.status_code, "VALIDATION_FAILED")
    return problem_response(request, code, str(exc))


@api.exception_handler(Exception)
def _internal(request: HttpRequest, exc: Exception) -> HttpResponse:
    log.exception("unhandled error on %s", request.path)
    return problem_response(request, "INTERNAL")


api.add_router("", oprf_router)
api.add_router("", snapshots_router)
api.add_router("", reports_router)
