"""Frozen core HTTP contract. Domain functions implement all money and authorization decisions."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

from django.contrib.auth import authenticate, login, logout
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.utils import timezone
from ninja import Body, Router

from apps.accounts.models import AgentToken, User
from apps.accounts.service import (
    fresh_step_up,
    issue_token,
    require,
    session_view,
    verify_otp,
)
from apps.agent.service import propose
from apps.approvals.models import Approval
from apps.approvals.service import approve, reject_or_veto, set_frozen, tenant_status
from apps.audit.chain import append_event
from apps.audit.models import AuditEvent
from apps.cashflow.models import Receivable
from apps.cashflow.service import alerts, analysis, financing, forecast, schedule
from apps.guardian.models import GuardianDecision
from apps.guardian.store import load_decision
from apps.invoices.models import Invoice
from apps.ledger.tenancy import bind_tenant
from apps.payments.errors import ProblemError
from apps.payments.idempotency import request_sha256, run_idempotent
from apps.payments.models import OutboxMessage, Payment
from apps.payments.service import create_payment, lock_tenant_limits
from apps.tenants.models import Tenant
from apps.vendors.models import BankAccountChange, Vendor, encrypt_iban
from apps.vendors.service import change_view, confirm_change, request_change, vendor_view

from .contracts import validate_payload

router = Router()
READ_SCOPES = ("T0", "T1", "T2")


def _response(body: Any, status: int = 200) -> HttpResponse:
    if status == 204:
        return HttpResponse(status=status)
    return JsonResponse(body, safe=not isinstance(body, list), status=status)


def _write(
    request: HttpRequest, tenant_id: uuid.UUID, handler: Callable[[], tuple[int, Any]]
) -> HttpResponse:
    result = run_idempotent(
        tenant_id,
        request.headers.get("Idempotency-Key"),
        request_sha256(request.method or "", request.path, request.body),
        handler,
    )
    return _response(result.body, 200 if result.replayed and result.status == 201 else result.status)


def _get_payment(tenant_id: uuid.UUID, payment_id: uuid.UUID) -> Payment:
    row = Payment.objects.filter(tenant_id=tenant_id, id=payment_id).first()
    if row is None:
        raise ProblemError("NOT_FOUND", "Payment not found")
    return row


def _get_vendor(tenant_id: uuid.UUID, vendor_id: uuid.UUID) -> Vendor:
    row = Vendor.objects.filter(tenant_id=tenant_id, id=vendor_id).first()
    if row is None:
        raise ProblemError("NOT_FOUND", "Vendor not found")
    return row


def payment_view(payment: Payment) -> dict[str, Any]:
    data: dict[str, Any] = {
        "payment_id": str(payment.id),
        "invoice_id": str(payment.invoice_id),
        "state": payment.state,
        "vendor_id": str(payment.vendor_id) if payment.vendor_id else None,
        "account_id": str(payment.account_id) if payment.account_id else None,
        "amount_minor": payment.amount_minor,
        "currency": payment.currency,
        "reference": payment.reference,
        "due_date": payment.due_date.isoformat() if payment.due_date else None,
        "tier": payment.tier,
        "decision_id": str(payment.decision_id) if payment.decision_id else None,
        "initiated_by": payment.initiated_by,
        "approvals_required": payment.approvals_required,
        "approval_warnings": [
            {"code": code, "detail": "Paying would breach the deterministic cash buffer."}
            for code in payment.approval_warnings
        ],
        "reject_reason": payment.reject_reason,
        "payee_reportable": payment.payee_reportable,
        "cooloff_until": payment.cooloff_until.isoformat() if payment.cooloff_until else None,
        "created_at": payment.created_at.isoformat(),
        "updated_at": payment.updated_at.isoformat(),
    }
    data["approvals"] = [
        {
            "approver_id": str(row.approver_id),
            "action": row.action,
            "reason": row.reason,
            "reject_reason": row.reject_reason or None,
            "acknowledged_warnings": row.acknowledged_warnings,
            "at": row.at.isoformat(),
        }
        for row in Approval.objects.filter(tenant_id=payment.tenant_id, payment_id=payment.id).order_by("at")
    ]
    data["history"] = [
        {
            "from": row.data.get("from"),
            "to": row.data["to"],
            "trigger": row.data["trigger"],
            "at": row.occurred_at.isoformat(),
            "actor": row.actor,
            "audit_seq": row.seq,
        }
        for row in AuditEvent.objects.filter(
            tenant_id=payment.tenant_id, subject_id=str(payment.id), action="PAYMENT_TRANSITION"
        ).order_by("seq")
    ]
    return data


@router.get("/auth/csrf", auth=None)
def csrf_token(request: HttpRequest) -> HttpResponse:
    return _response({"csrf_token": get_token(request)})


@router.post("/auth/login", auth=None)
def auth_login(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    validate_payload(payload, "LoginRequest")
    user = authenticate(request, username=payload["username"], password=payload["password"])
    if not isinstance(user, User) or user.tenant_id is None or not verify_otp(user, payload["otp"]):
        raise ProblemError("UNAUTHENTICATED", "Invalid credentials or one-time password")
    login(request, user)
    request.session["step_up_at"] = timezone.now().isoformat()
    bind_tenant(user.tenant_id)
    get_token(request)
    return _response(session_view(request, user))


@router.get("/auth/session")
def auth_session(request: HttpRequest) -> HttpResponse:
    principal = require(request)
    if principal.user is None:
        raise ProblemError("UNAUTHENTICATED", "Human session required")
    get_token(request)
    return _response(session_view(request, principal.user))


@router.post("/auth/logout")
def auth_logout(request: HttpRequest) -> HttpResponse:
    require(request)
    logout(request)
    return _response(None, 204)


@router.post("/auth/step-up")
def auth_step_up(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request)
    validate_payload(payload, "StepUpRequest")
    if principal.user is None or not verify_otp(principal.user, payload["otp"]):
        raise ProblemError("UNAUTHENTICATED", "Invalid or replayed one-time password")
    request.session["step_up_at"] = timezone.now().isoformat()
    return _response(session_view(request, principal.user))


@router.post("/auth/agent-tokens")
def create_token(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request, roles=("owner",), step_up=True)
    validate_payload(payload, "AgentTokenCreate")

    def handle() -> tuple[int, Any]:
        limits = lock_tenant_limits(principal.tenant_id)
        if limits.frozen:
            raise ProblemError("TENANT_FROZEN", "Cannot issue tokens while frozen")
        result = issue_token(
            principal.tenant_id,
            uuid.UUID(principal.actor.id),
            payload["name"],
            payload["scopes"],
            payload.get("ttl_seconds", 3600),
        )
        append_event(
            tenant_id=principal.tenant_id,
            actor=principal.actor.as_dict(),
            action="AGENT_TOKEN_ISSUED",
            subject_type="token",
            subject_id=result["token_id"],
            data={"scopes": result["scopes"]},
        )
        return 201, result

    return _write(request, principal.tenant_id, handle)


@router.delete("/auth/agent-tokens/{token_id}")
def revoke_token(request: HttpRequest, token_id: uuid.UUID) -> HttpResponse:
    principal = require(request, roles=("owner", "approver"))
    row = AgentToken.objects.filter(tenant_id=principal.tenant_id, id=token_id).first()
    if row is None:
        raise ProblemError("NOT_FOUND", "Agent token not found")
    if row.revoked_at is None:
        row.revoked_at = timezone.now()
        row.save(update_fields=["revoked_at"])
        append_event(
            tenant_id=principal.tenant_id,
            actor=principal.actor.as_dict(),
            action="AGENT_TOKEN_REVOKED",
            subject_type="token",
            subject_id=str(row.id),
            data={},
        )
    return _response(None, 204)


@router.get("/vendors")
def list_vendors(request: HttpRequest, q: str = "") -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    rows = Vendor.objects.filter(tenant_id=principal.tenant_id, name__icontains=q).order_by("name")[:1000]
    return _response([vendor_view(row) for row in rows])


@router.get("/vendors/{vendor_id}")
def get_vendor(request: HttpRequest, vendor_id: uuid.UUID) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    return _response(vendor_view(_get_vendor(principal.tenant_id, vendor_id)))


@router.post("/vendors")
def create_vendor(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request, roles=("owner",), step_up=True)
    validate_payload(payload, "VendorCreate")

    def handle() -> tuple[int, Any]:
        lock_tenant_limits(principal.tenant_id)
        vendor = Vendor.objects.create(tenant_id=principal.tenant_id, **payload)
        append_event(
            tenant_id=principal.tenant_id,
            actor=principal.actor.as_dict(),
            action="VENDOR_CREATED",
            subject_type="vendor",
            subject_id=str(vendor.id),
            data={"name": vendor.name},
        )
        return 201, vendor_view(vendor)

    return _write(request, principal.tenant_id, handle)


@router.post("/vendors/{vendor_id}/bank-account-change")
def bank_change_request(
    request: HttpRequest, vendor_id: uuid.UUID, payload: Body[dict[str, Any]]
) -> HttpResponse:
    principal = require(request, roles=("owner",), step_up=True)
    validate_payload(payload, "BankAccountChangeRequest")
    vendor = _get_vendor(principal.tenant_id, vendor_id)
    return _write(
        request,
        principal.tenant_id,
        lambda: (201, change_view(request_change(vendor, payload, principal.actor))),
    )


@router.post("/vendors/{vendor_id}/bank-account-change/{change_id}/confirm")
def bank_change_confirm(
    request: HttpRequest, vendor_id: uuid.UUID, change_id: uuid.UUID, payload: Body[dict[str, Any]]
) -> HttpResponse:
    principal = require(request, roles=("owner", "approver"), step_up=True)
    validate_payload(payload, "BankAccountChangeConfirm")
    change = BankAccountChange.objects.filter(
        tenant_id=principal.tenant_id, vendor_id=vendor_id, id=change_id
    ).first()
    if change is None:
        raise ProblemError("NOT_FOUND", "Bank account change not found")
    return _write(
        request,
        principal.tenant_id,
        lambda: (200, change_view(confirm_change(change, payload, principal.actor))),
    )


@router.post("/invoices")
def ingest_invoice(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    validate_payload(payload, "Invoice", component=False)

    def handle() -> tuple[int, Any]:
        lock_tenant_limits(principal.tenant_id)
        invoice = Invoice.objects.create(
            tenant_id=principal.tenant_id,
            source={"ciphertext": encrypt_iban(json.dumps(payload), principal.tenant_id)},
            received_at=payload["received_at"],
        )
        payment = create_payment(principal.tenant_id, invoice.id, principal.actor)
        OutboxMessage.objects.create(
            tenant_id=principal.tenant_id,
            payment_id=payment.id,
            kind="invoice.extract",
            payload={"invoice_id": str(invoice.id), "payment_id": str(payment.id)},
            available_at=timezone.now(),
        )
        return 201, {
            "invoice_id": str(invoice.id),
            "payment_id": str(payment.id),
            "state": payment.state,
            "received_at": payload["received_at"],
            "extracted": None,
        }

    return _write(request, principal.tenant_id, handle)


@router.get("/invoices/{invoice_id}")
def get_invoice(request: HttpRequest, invoice_id: uuid.UUID) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    invoice = Invoice.objects.filter(tenant_id=principal.tenant_id, id=invoice_id).first()
    if invoice is None:
        raise ProblemError("NOT_FOUND", "Invoice not found")
    payment = Payment.objects.get(tenant_id=principal.tenant_id, invoice_id=invoice.id)
    return _response(
        {
            "invoice_id": str(invoice.id),
            "payment_id": str(payment.id),
            "state": payment.state,
            "received_at": invoice.received_at.isoformat(),
            "extracted": invoice.extracted,
        }
    )


@router.post("/agent/proposals")
def agent_proposal(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request, scopes=("T1", "T2"), agent_only=True)
    validate_payload(payload, "Proposal", component=False)
    return _write(
        request,
        principal.tenant_id,
        lambda: (201, propose(principal.tenant_id, payload, principal.actor).model_dump(mode="json")),
    )


@router.get("/payments")
def list_payments(
    request: HttpRequest, state: str | None = None, cursor: str | None = None, limit: int = 50
) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    if not 1 <= limit <= 200:
        raise ProblemError("VALIDATION_FAILED", "limit must be between 1 and 200")
    rows = Payment.objects.filter(tenant_id=principal.tenant_id)
    if state:
        from apps.payments.spec import state_machine  # noqa: PLC0415

        if state not in state_machine().states:
            raise ProblemError("VALIDATION_FAILED", "Unknown payment state")
        rows = rows.filter(state=state)
    rows = rows.order_by("-created_at", "-id")
    if cursor:
        try:
            before = rows.get(id=uuid.UUID(cursor))
        except (ValueError, Payment.DoesNotExist):
            raise ProblemError("VALIDATION_FAILED", "Invalid cursor") from None
        from django.db.models import Q  # noqa: PLC0415

        rows = rows.filter(
            Q(created_at__lt=before.created_at) | Q(created_at=before.created_at, id__lt=before.id)
        )
    page = list(rows[: limit + 1])
    return _response(
        {
            "items": [payment_view(row) for row in page[:limit]],
            "next_cursor": str(page[limit - 1].id) if len(page) > limit else None,
        }
    )


@router.get("/payments/{payment_id}")
def get_payment(request: HttpRequest, payment_id: uuid.UUID) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    return _response(payment_view(_get_payment(principal.tenant_id, payment_id)))


@router.get("/decisions/{decision_id}")
def get_decision(request: HttpRequest, decision_id: uuid.UUID) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    row = GuardianDecision.objects.filter(tenant_id=principal.tenant_id, decision_id=decision_id).first()
    if row is None:
        raise ProblemError("NOT_FOUND", "Decision not found")
    return _response(load_decision(row).model_dump(mode="json"))


@router.post("/payments/{payment_id}/approve")
def approve_payment(
    request: HttpRequest, payment_id: uuid.UUID, payload: Body[dict[str, Any]]
) -> HttpResponse:
    principal = require(request, roles=("owner", "approver"), step_up=True)
    validate_payload(payload, "ApproveBody")
    payment = _get_payment(principal.tenant_id, payment_id)
    return _write(
        request,
        principal.tenant_id,
        lambda: (
            200,
            payment_view(
                approve(
                    payment,
                    principal.actor,
                    principal.role or "",
                    fresh_step_up(request),
                    reason=payload["reason"],
                    acknowledged_warnings=payload.get("acknowledged_warnings", []),
                )
            ),
        ),
    )


@router.post("/payments/{payment_id}/reject")
def reject_payment(
    request: HttpRequest, payment_id: uuid.UUID, payload: Body[dict[str, Any]]
) -> HttpResponse:
    principal = require(request, roles=("owner", "approver"), step_up=True)
    validate_payload(payload, "RejectBody")
    payment = _get_payment(principal.tenant_id, payment_id)
    return _write(
        request,
        principal.tenant_id,
        lambda: (
            200,
            payment_view(
                reject_or_veto(
                    payment,
                    principal.actor,
                    principal.role or "",
                    fresh_step_up(request),
                    reason=payload["reason"],
                    reject_reason=payload["reject_reason"],
                )
            ),
        ),
    )


@router.post("/payments/{payment_id}/veto")
def veto_payment(request: HttpRequest, payment_id: uuid.UUID, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request, roles=("owner",), step_up=True)
    validate_payload(payload, "ReasonBody")
    payment = _get_payment(principal.tenant_id, payment_id)
    return _write(
        request,
        principal.tenant_id,
        lambda: (
            200,
            payment_view(
                reject_or_veto(
                    payment,
                    principal.actor,
                    principal.role or "",
                    fresh_step_up(request),
                    reason=payload["reason"],
                )
            ),
        ),
    )


def _tenant_match(request: HttpRequest, tenant_id: uuid.UUID, *, unfreeze: bool = False) -> Any:
    principal = require(request, roles=("owner",) if unfreeze else ("owner", "approver"), step_up=unfreeze)
    if tenant_id != principal.tenant_id:
        raise ProblemError("NOT_FOUND", "Tenant not found")
    return principal


@router.get("/tenants/{tenant_id}/status")
def get_tenant_status(request: HttpRequest, tenant_id: uuid.UUID) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    if tenant_id != principal.tenant_id:
        raise ProblemError("NOT_FOUND", "Tenant not found")
    return _response(tenant_status(tenant_id))


@router.post("/tenants/{tenant_id}/freeze")
def freeze_tenant(request: HttpRequest, tenant_id: uuid.UUID, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = _tenant_match(request, tenant_id)
    validate_payload(payload, "ReasonBody")
    return _write(
        request, tenant_id, lambda: (200, set_frozen(tenant_id, principal.actor, True, payload["reason"]))
    )


@router.post("/tenants/{tenant_id}/unfreeze")
def unfreeze_tenant(
    request: HttpRequest, tenant_id: uuid.UUID, payload: Body[dict[str, Any]]
) -> HttpResponse:
    principal = _tenant_match(request, tenant_id, unfreeze=True)
    validate_payload(payload, "ReasonBody")
    return _write(
        request, tenant_id, lambda: (200, set_frozen(tenant_id, principal.actor, False, payload["reason"]))
    )


@router.get("/cashflow/forecast")
def cashflow_forecast(request: HttpRequest, as_of: date | None = None) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    return _response(forecast(principal.tenant_id, as_of))


@router.get("/cashflow/schedule")
def cashflow_schedule(request: HttpRequest, as_of: date | None = None) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    return _response(schedule(principal.tenant_id, as_of))


@router.get("/analysis/{metric}")
def financial_analysis(
    request: HttpRequest, metric: str, period_from: date | None = None, period_to: date | None = None
) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    return _response(analysis(principal.tenant_id, metric, period_from, period_to))


@router.get("/alerts")
def cashflow_alerts(request: HttpRequest) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    result = alerts(principal.tenant_id)
    # Operational incidents use the same frozen Alert shape as forecast alerts.
    incidents = AuditEvent.objects.filter(
        tenant_id=principal.tenant_id,
        action__in=("RECON_MISMATCH", "MISSING_IN_BANK", "OUTBOX_INCIDENT", "TENANT_FROZEN"),
    )
    for event in incidents.order_by("-seq")[:100]:
        result.append(
            {
                "alert_id": str(event.event_id),
                "type": event.action,
                "severity": "CRITICAL",
                "week_start": None,
                "amount_minor": None,
                "created_at": event.occurred_at.isoformat(),
                "status": "OPEN",
                "detail": event.data.get("reason", event.action)[:500],
            }
        )
    return _response(result)


@router.post("/financing/match")
def financing_match(request: HttpRequest, payload: Body[dict[str, Any]]) -> HttpResponse:
    principal = require(request, scopes=READ_SCOPES)
    validate_payload(payload, "FinancingMatchRequest")

    def handle() -> tuple[int, Any]:
        receivables = sum(
            row.amount_minor
            for row in Receivable.objects.filter(tenant_id=principal.tenant_id, paid_on__isnull=True)
        )
        tenant = Tenant.objects.get(id=principal.tenant_id)
        return 200, financing(
            payload["shortfall_minor"],
            payload["currency"],
            date.fromisoformat(payload["needed_by"]),
            receivables_minor=receivables,
            history_days=(timezone.now() - tenant.created_at).days,
            max_term_days=payload.get("max_term_days", 1825),
        )

    return _write(request, principal.tenant_id, handle)


@router.get("/evidence/metrics")
def evidence_metrics(request: HttpRequest) -> HttpResponse:
    require(request, scopes=READ_SCOPES)
    from django.conf import settings  # noqa: PLC0415

    directory = Path(os.environ.get("SAFEPAY_EVIDENCE_DIR", str(settings.REPO_DIR / "evidence" / "run-42")))
    path = directory / "metrics.json"
    if not path.is_file():
        raise ProblemError("NOT_FOUND", "Generate the synthetic evidence bundle first")
    return _response(json.loads(path.read_text(encoding="utf-8")))
