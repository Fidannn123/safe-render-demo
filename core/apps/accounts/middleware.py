"""Bind RLS from authenticated credentials inside one request transaction, never a tenant header."""

from __future__ import annotations

from collections.abc import Callable

from django.db import transaction
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import CsrfViewMiddleware

from apps.ledger.tenancy import bind_tenant
from apps.payments.errors import ProblemError
from apps.payments.types import Actor

from .models import User
from .service import Principal, authenticate_token

SAFE_METHODS = ("GET", "HEAD", "OPTIONS", "TRACE")


class TenantMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.path.startswith("/api/"):
            # /healthz must answer (and report a degraded database) without a tenant transaction.
            request.principal = None  # type: ignore[attr-defined]
            return self.get_response(request)
        try:
            principal = None
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                principal = authenticate_token(authorization[7:])
            elif request.user.is_authenticated and isinstance(request.user, User) and request.user.tenant_id:
                principal = Principal(
                    request.user.tenant_id,
                    Actor("human", str(request.user.id)),
                    role=request.user.role,
                    user=request.user,
                )
            request.principal = principal  # type: ignore[attr-defined]
            if (
                principal is not None
                and principal.actor.type == "human"
                and request.method not in SAFE_METHODS
            ):
                rejected = CsrfViewMiddleware(self.get_response).process_view(request, _csrf_target, (), {})
                if rejected:
                    raise ProblemError("FORBIDDEN_SCOPE", "CSRF verification failed")
            if principal is None and request.method in SAFE_METHODS:
                # Anonymous reads (openapi.json, csrf) need no tenant transaction.
                return self.get_response(request)
            with transaction.atomic():
                if principal is not None:
                    bind_tenant(principal.tenant_id)
                response = self.get_response(request)
                # Ninja exception handlers return responses: roll back any writes before a typed error.
                if response.status_code >= 400:
                    transaction.set_rollback(True)
                return response
        except ProblemError as error:
            return JsonResponse(
                error.as_problem(), status=error.status, content_type="application/problem+json"
            )


def _csrf_target(request: HttpRequest) -> HttpResponse:
    return HttpResponse()
