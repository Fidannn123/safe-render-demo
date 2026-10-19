"""django-ninja API root for /api/v1. Routers are added by each module (contract: spec/openapi-core.yaml)."""

from __future__ import annotations

from django.http import HttpRequest, JsonResponse
from ninja import NinjaAPI
from ninja.errors import ValidationError

from apps.payments.errors import ProblemError

from .views import router

api = NinjaAPI(
    title="SafePay core API",
    version="1.0.0",
    urls_namespace="core-api",
    description="Implements spec/openapi-core.yaml. Synthetic data, simulated bank.",
    docs_url=None,  # default Swagger UI loads assets from a CDN (unreachable for some reviewers)
)


@api.exception_handler(ProblemError)
def problem_response(request: HttpRequest, error: ProblemError) -> JsonResponse:
    return JsonResponse(error.as_problem(), status=error.status, content_type="application/problem+json")


@api.exception_handler(ValidationError)
def validation_response(request: HttpRequest, error: ValidationError) -> JsonResponse:
    problem = ProblemError("VALIDATION_FAILED", "Request shape or parameter is invalid")
    return JsonResponse(problem.as_problem(), status=problem.status, content_type="application/problem+json")


api.add_router("", router)
