"""/v1/accounts/{account_id} (spec/openapi-bank.yaml). SIMULATED bank."""

from __future__ import annotations

import uuid
from typing import Annotated

from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Path, Router
from pydantic import Field

from apps.accounts.models import Account
from apps.accounts.services import account_json
from safepay_bank.problems import ProblemError
from safepay_bank.schemas import UUID_PATTERN

router = Router(tags=["accounts"])


@router.get("/accounts/{account_id}", operation_id="accounts_get")
def accounts_get(
    request: HttpRequest, account_id: Path[Annotated[str, Field(pattern=UUID_PATTERN)]]
) -> HttpResponse:
    account = Account.objects.filter(pk=uuid.UUID(account_id)).first()
    if account is None:
        raise ProblemError("ACCOUNT_NOT_FOUND", "account not found")
    return JsonResponse(account_json(account))
