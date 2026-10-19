"""GET /healthz: liveness plus database connectivity (spec: components.schemas.Health)."""

from __future__ import annotations

from django.conf import settings
from django.db import DatabaseError, connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_GET


@require_GET
def healthz(request: HttpRequest) -> JsonResponse:
    try:
        connection.ensure_connection()
        db = "ok"
    except DatabaseError:
        db = "unavailable"
    ok = db == "ok"
    body = {"status": "ok" if ok else "degraded", "service": settings.SERVICE_NAME, "db": db}
    return JsonResponse(body, status=200 if ok else 503)
