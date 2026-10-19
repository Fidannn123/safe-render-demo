"""GET /v1/epochs/current and POST /v1/oprf/evaluate (spec/openapi-intel.yaml, tag oprf)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from django.db import transaction
from django.http import HttpRequest
from ninja import Router, Schema
from pydantic import ConfigDict, Field

from apps.members.auth import MemberSignature, request_member
from apps.oprf.epochs import current_epoch, epoch_number, key_for, server_for
from apps.oprf.models import QuotaUsage
from apps.oprf.port import MAX_BATCH, OprfError
from safepay_intel.crypto import b64decode_strict, b64encode
from safepay_intel.policy import network_policy
from safepay_intel.problems import ProblemError

router = Router(tags=["oprf"], auth=MemberSignature())

ELEMENT_PATTERN = r"^[A-Za-z0-9+/]{43}=$"
Element = Annotated[str, Field(pattern=ELEMENT_PATTERN)]


def _rfc3339(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow() -> datetime:
    return datetime.now(UTC)


class EpochOut(Schema):
    epoch: int
    suite: Literal["ristretto255-SHA512"]
    mode: Literal["VOPRF"]
    oprf_public_key: str
    starts_at: str
    ends_at: str


class EvaluateIn(Schema):
    model_config = ConfigDict(extra="forbid")
    epoch: int = Field(ge=0)
    blinded: list[Element] = Field(min_length=1, max_length=MAX_BATCH)


class EvaluateOut(Schema):
    epoch: int
    evaluated: list[str]
    proof: str
    quota_remaining: int


@router.get("/epochs/current", response=EpochOut, operation_id="epochs_current")
def epochs_current(request: HttpRequest) -> EpochOut:
    e = current_epoch(utcnow())
    return EpochOut(
        epoch=e.epoch,
        suite="ristretto255-SHA512",
        mode="VOPRF",
        oprf_public_key=b64encode(e.public_key),
        starts_at=_rfc3339(e.starts_at),
        ends_at=_rfc3339(e.ends_at),
    )


@router.post("/oprf/evaluate", response=EvaluateOut, operation_id="oprf_evaluate")
def oprf_evaluate(request: HttpRequest, payload: EvaluateIn) -> EvaluateOut:
    ctx = request_member(request)
    now = utcnow()
    if payload.epoch != epoch_number(now):
        raise ProblemError("UNKNOWN_EPOCH", "epoch is not current")
    key = key_for(payload.epoch, create=True)
    if key is None:  # pragma: no cover
        raise ProblemError("UNKNOWN_EPOCH")
    blinded = [b64decode_strict(b) for b in payload.blinded]
    quota = network_policy().oprf_quota_per_member_per_day
    with transaction.atomic():
        QuotaUsage.objects.get_or_create(member=ctx.member, day=now.date())
        usage = QuotaUsage.objects.select_for_update().get(member=ctx.member, day=now.date())
        if usage.count + len(blinded) > quota:
            raise ProblemError(
                "QUOTA_EXCEEDED", f"{quota} evaluations per member per day; {quota - usage.count} left"
            )
        try:
            result = server_for(key).blind_evaluate(blinded)
        except OprfError as exc:
            raise ProblemError(
                "VALIDATION_FAILED",
                "invalid blinded element",
                [{"loc": ["body", "blinded"], "msg": str(exc)}],
            ) from exc
        usage.count += len(blinded)
        usage.save(update_fields=["count"])
    return EvaluateOut(
        epoch=payload.epoch,
        evaluated=[b64encode(e) for e in result.evaluated],
        proof=b64encode(result.proof),
        quota_remaining=quota - usage.count,
    )
