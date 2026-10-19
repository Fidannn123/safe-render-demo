"""/v1/transfers and /admin/fail-next (spec/openapi-bank.yaml). SIMULATED bank."""

from __future__ import annotations

import logging
import time
import uuid
from typing import Annotated

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from ninja import Path, Router
from pydantic import Field

from apps.transfers.models import Transfer
from apps.transfers.services import arm_fail_next, get_fail_next, submit_transfer, transfer_json
from safepay_bank.problems import ProblemError
from safepay_bank.schemas import UUID_PATTERN, FailNextRequestIn, TransferRequestIn

log = logging.getLogger("safepay_bank.transfers")

router = Router(tags=["transfers"])
admin_router = Router(tags=["admin"])

sleep = time.sleep  # patched in tests


@router.post("/transfers", operation_id="transfers_create")
def transfers_create(request: HttpRequest, payload: TransferRequestIn) -> HttpResponse:
    body = payload.model_dump(mode="json", exclude_unset=True)
    result = submit_transfer(body)
    response = JsonResponse(transfer_json(result.transfer), status=201 if result.created else 200)
    if result.delay_response:
        # SIMULATION ONLY (mode=timeout, D-028): the transfer is committed, the answer is withheld.
        log.warning("fail-next timeout: withholding response for %s", result.transfer.end_to_end_id)
        sleep(float(settings.SAFEPAY_BANK_TIMEOUT_SECONDS))
    return response


@router.get("/transfers/{end_to_end_id}", operation_id="transfers_get")
def transfers_get(
    request: HttpRequest, end_to_end_id: Path[Annotated[str, Field(pattern=UUID_PATTERN)]]
) -> HttpResponse:
    transfer = Transfer.objects.filter(end_to_end_id=uuid.UUID(end_to_end_id)).first()
    if transfer is None:
        raise ProblemError("NOT_FOUND", "transfer not found")
    return JsonResponse(transfer_json(transfer))


def _fail_next_json() -> dict[str, object]:
    arm = get_fail_next()
    return {"remaining": arm.remaining, "reason": arm.reason, "mode": arm.mode}


@admin_router.post("/fail-next", operation_id="admin_fail_next")
def admin_fail_next(request: HttpRequest, payload: FailNextRequestIn) -> HttpResponse:
    arm_fail_next(payload.count, payload.reason, payload.mode)
    log.warning(
        "SIMULATION: fail-next armed count=%s reason=%s mode=%s", payload.count, payload.reason, payload.mode
    )
    return JsonResponse(_fail_next_json())
