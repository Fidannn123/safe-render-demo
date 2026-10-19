"""POST /v1/reports and DELETE /v1/reports/{report_id} (spec/openapi-intel.yaml, tag reports)."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from django.http import HttpRequest, HttpResponse
from ninja import Router, Status

from apps.members.auth import MemberSignature, request_member
from apps.reports.admission import admit, report_status, retract
from apps.reports.schemas import ReportIn, ReportRecordOut
from safepay_intel.problems import ProblemError

router = Router(tags=["reports"], auth=MemberSignature())

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def utcnow() -> datetime:
    return datetime.now(UTC)


@router.post("/reports", response={200: ReportRecordOut, 201: ReportRecordOut}, operation_id="reports_submit")
def reports_submit(request: HttpRequest, payload: ReportIn) -> Status[ReportRecordOut]:
    ctx = request_member(request)
    raw: Any = json.loads(request.body)  # signatures cover the exact submitted fields, not a re-dump
    if not isinstance(raw, dict) or not isinstance(raw.get("receipt"), dict):
        raise ProblemError("VALIDATION_FAILED")
    now = utcnow()
    result = admit(ctx, payload, raw, now)
    report = result.report
    state = report_status(report, now)
    record = ReportRecordOut(
        report_id=str(report.report_id),
        status=state,
        payee_counted=state == "ADMITTED",
        expires_at=report.expires_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return Status(201 if result.created else 200, record)


@router.delete("/reports/{report_id}", response={204: None}, operation_id="reports_retract")
def reports_retract(request: HttpRequest, report_id: str) -> HttpResponse:
    ctx = request_member(request)
    if not _UUID.match(report_id):
        raise ProblemError("NOT_FOUND", "report not found")
    retract(ctx, report_id, utcnow())
    return HttpResponse(status=204)
