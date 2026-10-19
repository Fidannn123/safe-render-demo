"""Human authorization and freeze controls, serialized with all payment transitions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.accounts.models import AgentToken
from apps.agent.service import payment_history, registry_snapshot
from apps.audit.chain import append_event
from apps.cashflow.service import forecast
from apps.guardian.inputs import ForecastBuffer, VendorSnapshot
from apps.guardian.models import GuardianDecision
from apps.guardian.policy import load_policy
from apps.guardian.recheck import ApprovalContext, recheck_for_approval
from apps.ledger.service import balance_of
from apps.payments.errors import ProblemError
from apps.payments.models import Payment, TenantLimits
from apps.payments.service import lock_tenant_limits, transition
from apps.payments.types import Actor, TransitionContext
from apps.vendors.models import Vendor

from .models import Approval


def accountable_initiator(payment: Payment) -> str | None:
    """The human answerable for a payment: proposals come from agent tokens, so it is the token's issuer.

    Comparing an approver with the raw initiator would compare a user id with a token id and never
    match, letting the person who delegated to the agent approve the agent's own proposal.
    """
    initiated = payment.initiated_by or {}
    if initiated.get("type") == "agent":
        token = AgentToken.objects.filter(tenant_id=payment.tenant_id, id=str(initiated.get("id"))).first()
        return str(token.created_by) if token else None
    value = initiated.get("id")
    return str(value) if value else None


def approve(
    payment: Payment,
    actor: Actor,
    role: str,
    step_up_at: datetime,
    *,
    reason: str,
    acknowledged_warnings: list[str],
) -> Payment:
    with transaction.atomic():
        limits = lock_tenant_limits(payment.tenant_id)
        payment = Payment.objects.select_for_update().get(id=payment.id, tenant_id=payment.tenant_id)
        if payment.state != "HELD":
            raise ProblemError("ILLEGAL_TRANSITION", "Only HELD payments accept approvals")
        initiator = accountable_initiator(payment)
        if initiator == actor.id:
            raise ProblemError("SELF_APPROVAL", "The initiator cannot approve their own payment")
        if Approval.objects.filter(
            tenant_id=payment.tenant_id, payment_id=payment.id, approver_id=actor.id, action="APPROVE"
        ).exists():
            raise ProblemError("ALREADY_APPROVED_BY_USER", "Your approval was already recorded")
        if limits.frozen:
            raise ProblemError("TENANT_FROZEN", "Unfreeze before approving")
        missing = set(payment.approval_warnings) - set(acknowledged_warnings)
        if missing:
            raise ProblemError("WARNING_NOT_ACKNOWLEDGED", f"Acknowledge {sorted(missing)}")
        approval = Approval.objects.create(
            tenant_id=payment.tenant_id,
            payment_id=payment.id,
            approver_id=actor.id,
            initiator_id=uuid.UUID(initiator) if initiator else uuid.UUID(int=0),
            action="APPROVE",
            reason=reason,
            acknowledged_warnings=acknowledged_warnings,
        )
        prior = list(
            Approval.objects.filter(
                tenant_id=payment.tenant_id, payment_id=payment.id, action="APPROVE"
            ).order_by("at")
        )
        append_event(
            tenant_id=payment.tenant_id,
            actor=actor.as_dict(),
            action="HUMAN_APPROVAL",
            subject_type="payment",
            subject_id=str(payment.id),
            data={
                "approval_id": str(approval.id),
                "reason": reason,
                "acknowledged_warnings": acknowledged_warnings,
            },
        )
        if len(prior) < payment.approvals_required:
            return payment
        if payment.decision_id is None or payment.vendor_id is None:
            raise ProblemError("ILLEGAL_TRANSITION", "A held payment must carry its Decision and vendor")
        decision = GuardianDecision.objects.get(tenant_id=payment.tenant_id, decision_id=payment.decision_id)
        vendor = Vendor.objects.get(tenant_id=payment.tenant_id, id=payment.vendor_id)
        projected = forecast(payment.tenant_id)
        result = recheck_for_approval(
            decision.inputs_canonical.encode(),
            decision.inputs_sha256,
            ApprovalContext(
                vendor=VendorSnapshot(vendor_id=vendor.id, name=vendor.name, blocked=vendor.blocked),
                registry_account=registry_snapshot(vendor.bank_account),
                history=payment_history(payment.tenant_id, payment.id),
                tenant_frozen=limits.frozen,
                bank_available_minor=balance_of(
                    payment.tenant_id, "Bank:available", payment.currency or "USD"
                ),
                now=timezone.now(),
                acknowledged_warnings=acknowledged_warnings,
                forecast=ForecastBuffer(
                    buffer_minor=projected["buffer_minor"],
                    projected_min_balance_minor=min(row["closing_minor"] for row in projected["weeks"]),
                ),
            ),
            load_policy(),
        )
        if not result.ok:
            raise ProblemError(
                str(result.error_code),
                "Final approval recheck failed: "
                + ", ".join(result.failed_hard_rules or result.unacknowledged_warnings),
            )
        return transition(
            payment,
            "APPROVED",
            "approvals_complete",
            actor,
            TransitionContext(
                reason=reason,
                step_up_at=step_up_at,
                actor_role=role,
                approver_ids=tuple(str(row.approver_id) for row in prior),
                acknowledged_warnings=tuple(acknowledged_warnings),
                attested=frozenset({"hard_rules_pass", "payee_account_unchanged"}),
            ),
        )


def reject_or_veto(
    payment: Payment,
    actor: Actor,
    role: str,
    step_up_at: datetime,
    *,
    reason: str,
    reject_reason: str | None = None,
) -> Payment:
    with transaction.atomic():
        lock_tenant_limits(payment.tenant_id)
        veto = reject_reason is None
        updated = transition(
            payment,
            "FAILED" if veto else "REJECTED",
            "t2_veto" if veto else "human_reject",
            actor,
            TransitionContext(
                reason=reason, reject_reason=reject_reason, step_up_at=step_up_at, actor_role=role
            ),
        )
        initiator = accountable_initiator(payment)
        # Rejection/veto may be made by an initiator; the database self-approval check protects APPROVE.
        Approval.objects.create(
            tenant_id=payment.tenant_id,
            payment_id=payment.id,
            approver_id=actor.id,
            initiator_id=(uuid.UUID(initiator) if initiator and initiator != actor.id else uuid.UUID(int=0)),
            action="VETO" if veto else "REJECT",
            reason=reason,
            reject_reason=reject_reason or "",
        )
        return updated


def tenant_status(tenant_id: uuid.UUID) -> dict[str, Any]:
    limits = TenantLimits.objects.filter(tenant_id=tenant_id).first()
    from apps.audit.models import AuditEvent  # noqa: PLC0415

    latest = AuditEvent.objects.filter(tenant_id=tenant_id, action__in=("TENANT_FROZEN", "TENANT_UNFROZEN"))
    event = latest.order_by("-seq").first()
    return {
        "tenant_id": str(tenant_id),
        "frozen": bool(limits and limits.frozen),
        "frozen_at": limits.frozen_at.isoformat() if limits and limits.frozen_at else None,
        "frozen_by": event.actor if event and limits and limits.frozen else None,
        "reason": event.data.get("reason") if event else None,
    }


def set_frozen(tenant_id: uuid.UUID, actor: Actor, frozen: bool, reason: str) -> dict[str, Any]:
    with transaction.atomic():
        limits = lock_tenant_limits(tenant_id)
        if limits.frozen != frozen:
            limits.frozen, limits.frozen_at = frozen, timezone.now() if frozen else None
            limits.save(update_fields=["frozen", "frozen_at", "updated_at"])
            if frozen:
                AgentToken.objects.filter(tenant_id=tenant_id, revoked_at__isnull=True).update(
                    revoked_at=timezone.now()
                )
            append_event(
                tenant_id=tenant_id,
                actor=actor.as_dict(),
                action="TENANT_FROZEN" if frozen else "TENANT_UNFROZEN",
                subject_type="tenant",
                subject_id=str(tenant_id),
                data={"reason": reason},
            )
        return tenant_status(tenant_id)
