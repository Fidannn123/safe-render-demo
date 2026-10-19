"""Dual-person call-back attestation is mandatory before any registry account changes."""

from __future__ import annotations

import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from apps.audit.chain import append_event
from apps.guardian.rules import payee_new_or_changed
from apps.payments.errors import ProblemError
from apps.payments.service import lock_tenant_limits
from apps.payments.types import Actor

from .models import BankAccount, BankAccountChange, Vendor, decrypt_iban, encrypt_iban


def mask(iban: str) -> str:
    return iban[:4] + "*" * (len(iban) - 8) + iban[-4:]


def vendor_view(vendor: Vendor) -> dict[str, Any]:
    from apps.agent.service import registry_snapshot  # noqa: PLC0415

    account = vendor.bank_account
    verified = None
    if account:
        verified = {
            "account_id": str(account.id),
            "country": account.country,
            "iban_masked": mask(account.iban),
            "holder_name": account.holder_name,
            "verified_at": account.verified_at.isoformat(),
            "changed_at": account.changed_at.isoformat(),
            "first_used_at": account.first_used_at.isoformat() if account.first_used_at else None,
            "is_new_or_changed": payee_new_or_changed(registry_snapshot(account), timezone.now(), 30),
        }
    return {
        "vendor_id": str(vendor.id),
        "name": vendor.name,
        "tax_id": vendor.tax_id,
        "status": "BLOCKED" if vendor.blocked else "ACTIVE",
        "expense_account": vendor.expense_account,
        "criticality": vendor.criticality,
        "late_fee_bp": vendor.late_fee_bp,
        "verified_account": verified,
        "created_at": vendor.created_at.isoformat(),
    }


def change_view(change: BankAccountChange) -> dict[str, Any]:
    return {
        "change_id": str(change.id),
        "vendor_id": str(change.vendor_id),
        "status": change.status,
        "requested_by": str(change.requested_by),
        "requested_at": change.requested_at.isoformat(),
        "confirmed_by": str(change.confirmed_by) if change.confirmed_by else None,
        "confirmed_at": change.confirmed_at.isoformat() if change.confirmed_at else None,
        "new_account_masked": mask(decrypt_iban(change.iban_encrypted, change.tenant_id)),
    }


def request_change(vendor: Vendor, payload: dict[str, Any], actor: Actor) -> BankAccountChange:
    account = payload["new_account"]
    with transaction.atomic():
        lock_tenant_limits(vendor.tenant_id)
        change = BankAccountChange.objects.create(
            tenant_id=vendor.tenant_id,
            vendor=vendor,
            requested_by=actor.id,
            country=account["country"],
            holder_name=account["holder_name"],
            iban_encrypted=encrypt_iban(account["iban"], vendor.tenant_id),
            reason=payload["reason"],
            request_attestation=payload["callback_attestation"],
        )
        append_event(
            tenant_id=vendor.tenant_id,
            actor=actor.as_dict(),
            action="REGISTRY_CHANGE_REQUESTED",
            subject_type="vendor",
            subject_id=str(vendor.id),
            data={
                "change_id": str(change.id),
                "reason": payload["reason"],
                "callback_attestation": payload["callback_attestation"],
            },
        )
        return change


def confirm_change(change: BankAccountChange, payload: dict[str, Any], actor: Actor) -> BankAccountChange:
    with transaction.atomic():
        lock_tenant_limits(change.tenant_id)
        change = BankAccountChange.objects.select_for_update().get(id=change.id, tenant_id=change.tenant_id)
        if str(change.requested_by) == actor.id:
            raise ProblemError("SECOND_PERSON_REQUIRED", "A different human must confirm this change")
        if change.status != "PENDING_CONFIRMATION":
            raise ProblemError("ILLEGAL_TRANSITION", "Registry change is no longer pending")
        BankAccount.objects.filter(
            tenant_id=change.tenant_id, vendor_id=change.vendor_id, active=True
        ).update(active=False)
        BankAccount.objects.create(
            tenant_id=change.tenant_id,
            vendor_id=change.vendor_id,
            country=change.country,
            holder_name=change.holder_name,
            iban_encrypted=change.iban_encrypted,
        )
        change.status, change.confirmed_by, change.confirmed_at = (
            "APPLIED",
            uuid.UUID(actor.id),
            timezone.now(),
        )
        change.confirm_attestation = payload["callback_attestation"]
        change.save()
        append_event(
            tenant_id=change.tenant_id,
            actor=actor.as_dict(),
            action="REGISTRY_CHANGE_APPLIED",
            subject_type="vendor",
            subject_id=str(change.vendor_id),
            data={
                "change_id": str(change.id),
                "reason": payload["reason"],
                "callback_attestation": payload["callback_attestation"],
            },
        )
        return change
