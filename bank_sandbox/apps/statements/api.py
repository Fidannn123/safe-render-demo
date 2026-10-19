"""/v1/statements?account_id&date (spec/openapi-bank.yaml). SIMULATED bank."""

from __future__ import annotations

import uuid
from datetime import date as date_type
from typing import Annotated

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Query, Router
from pydantic import Field

from apps.statements.services import get_statement
from safepay_bank.problems import ProblemError
from safepay_bank.schemas import DATE_PATTERN, UUID_PATTERN

router = Router(tags=["statements"])


@router.get("/statements", operation_id="statements_get")
def statements_get(
    request: HttpRequest,
    account_id: Query[Annotated[str, Field(pattern=UUID_PATTERN)]],
    date: Query[Annotated[str, Field(pattern=DATE_PATTERN)]],
) -> HttpResponse:
    try:
        day = date_type.fromisoformat(date)
    except ValueError as exc:
        raise ProblemError(
            "VALIDATION_FAILED", "invalid date", errors=[{"loc": ["query", "date"], "msg": str(exc)}]
        ) from exc
    return JsonResponse(get_statement(uuid.UUID(account_id), day))
