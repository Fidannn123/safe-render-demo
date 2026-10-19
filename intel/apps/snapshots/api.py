"""GET /v1/snapshots/{epoch} (spec/openapi-intel.yaml, tag snapshots)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from django.http import HttpRequest, JsonResponse
from ninja import Router

from apps.members.auth import MemberSignature, request_member
from apps.snapshots.service import snapshot_for
from safepay_intel.problems import ProblemError

router = Router(tags=["snapshots"], auth=MemberSignature())


@router.get("/snapshots/{epoch}", response={200: dict[str, Any]}, operation_id="snapshots_get")
def snapshots_get(request: HttpRequest, epoch: int) -> JsonResponse:
    request_member(request)
    if epoch < 0:
        raise ProblemError("NOT_FOUND", "no snapshot for this epoch")
    body = snapshot_for(epoch, datetime.now(UTC))
    if body is None:
        raise ProblemError("NOT_FOUND", "no snapshot for this epoch")
    return JsonResponse(body)
