"""Typed planner tools and the transactional agent -> Guardian -> ledger integration."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.cashflow.service import forecast
from apps.guardian.decide import Decision, issue_decision
from apps.guardian.inputs import (
    ForecastBuffer,
    GuardianInputs,
    HistoryPayment,
    InvoiceExtraction,
    KnownAccount,
    KnownVendor,
    NetworkResult,
    ProposalInput,
    RegistryAccount,
    RollingTotals,
    VendorSnapshot,
)
from apps.guardian.policy import load_policy
from apps.guardian.rules import COUNTED_STATES, payee_new_or_changed
from apps.guardian.store import record_decision
from apps.invoices.models import Invoice
from apps.keystore.file_store import default_keystore
from apps.payments.errors import ProblemError
from apps.payments.fingerprint import invoice_fingerprint
from apps.payments.models import Payment
from apps.payments.service import lock_tenant_limits, transition
from apps.payments.types import (
    SYSTEM,
    Actor,
    DecisionBinding,
    ExtractionBinding,
    ProposalBinding,
    TransitionContext,
)
from apps.vendors.models import BankAccount, Vendor, decrypt_iban

from .extractor import ExtractedInvoice, extract


def registry_snapshot(account: BankAccount | None) -> RegistryAccount | None:
    if account is None:
        return None
    return RegistryAccount(
        account_id=account.id,
        vendor_id=account.vendor_id,
        country=account.country,
        iban=account.iban,
        changed_at=account.changed_at,
        first_used_at=account.first_used_at,
    )


def payment_history(tenant_id: uuid.UUID, exclude: uuid.UUID | None = None) -> tuple[HistoryPayment, ...]:
    rows = Payment.objects.filter(
        tenant_id=tenant_id,
        vendor_id__isnull=False,
        account_id__isnull=False,
        amount_minor__isnull=False,
        invoice_fingerprint__isnull=False,
    )
    if exclude:
        rows = rows.exclude(id=exclude)
    return tuple(
        HistoryPayment.model_validate(
            {
                "payment_id": row.id,
                "vendor_id": row.vendor_id,
                "account_id": row.account_id,
                "amount_minor": row.amount_minor,
                "currency": row.currency,
                "reference": row.reference or "",
                "invoice_fingerprint": row.invoice_fingerprint,
                "state": row.state,
                "created_at": row.created_at,
            }
        )
        for row in rows
    )


def extract_invoice(invoice_id: uuid.UUID, tenant_id: uuid.UUID) -> Invoice:
    with transaction.atomic():
        lock_tenant_limits(tenant_id)
        invoice = Invoice.objects.select_for_update().get(id=invoice_id, tenant_id=tenant_id)
        payment = Payment.objects.get(invoice_id=invoice.id, tenant_id=tenant_id)
        if invoice.extracted is not None:
            return invoice
        source = json.loads(decrypt_iban(invoice.source["ciphertext"], tenant_id))
        extracted = extract(invoice.id, source)
        invoice.extracted = extracted.model_dump(mode="json", exclude_none=True)
        invoice.save(update_fields=["extracted"])
        vendors = list(Vendor.objects.filter(tenant_id=tenant_id, name__iexact=extracted.vendor_name))
        vendor = vendors[0] if len(vendors) == 1 else None
        transition(
            payment,
            "EXTRACTED",
            "extraction_validated",
            SYSTEM,
            TransitionContext(
                extraction=ExtractionBinding(
                    vendor.id if vendor else None,
                    extracted.amount_minor,
                    extracted.currency,
                    extracted.reference,
                    extracted.due_date,
                    vendor.expense_account if vendor else "Expense",
                )
            ),
        )
        return invoice


def plan_payment(extracted: ExtractedInvoice, vendor: Vendor) -> dict[str, Any]:
    """The planner accepts typed fields only; neither raw text nor claimed bank details are arguments."""
    account = vendor.bank_account
    if account is None:
        raise ProblemError("VALIDATION_FAILED", "Vendor requires a verified registry account")
    return {
        "invoice_id": str(extracted.invoice_id),
        "vendor_id": str(vendor.id),
        "account_id": str(account.id),
        "amount_minor": extracted.amount_minor,
        "currency": extracted.currency,
        "reference": extracted.reference,
        "due_date": extracted.due_date.isoformat(),
    }


def propose(tenant_id: uuid.UUID, payload: dict[str, Any], actor: Actor) -> Decision:  # noqa: PLR0915
    with transaction.atomic():
        limits = lock_tenant_limits(tenant_id)
        invoice = Invoice.objects.filter(tenant_id=tenant_id, id=payload["invoice_id"]).first()
        if invoice is None:
            raise ProblemError("NOT_FOUND", "Invoice not found")
        if invoice.extracted is None:
            raise ProblemError("INVOICE_NOT_EXTRACTED", "Wait for the invoice extractor")
        payment = Payment.objects.get(tenant_id=tenant_id, invoice_id=invoice.id)
        if payment.state not in ("EXTRACTED", "REJECTED"):
            raise ProblemError("ILLEGAL_TRANSITION", "Invoice cannot be proposed in its current state")
        vendor = Vendor.objects.filter(tenant_id=tenant_id, id=payload["vendor_id"]).first()
        if vendor is None:
            raise ProblemError("NOT_FOUND", "Vendor not found")
        extracted = ExtractedInvoice.model_validate(invoice.extracted)
        # This boundary binds the amount, reference and due date to the validated source.
        if (payload["amount_minor"], payload["currency"], payload["reference"], payload["due_date"]) != (
            extracted.amount_minor,
            extracted.currency,
            extracted.reference,
            extracted.due_date.isoformat(),
        ):
            raise ProblemError("VALIDATION_FAILED", "Proposal differs from the extracted invoice")
        if vendor.name.casefold() != extracted.vendor_name.casefold():
            raise ProblemError("VALIDATION_FAILED", "Vendor does not match the extracted registry identity")
        if (
            payload.get("requested_execution_date")
            and payload["requested_execution_date"] < payload["due_date"]
        ):
            from apps.cashflow.service import schedule  # noqa: PLC0415

            allowed = next(
                (
                    row
                    for row in schedule(tenant_id)["items"]
                    if row["invoice_id"] == str(invoice.id) and row["reason"] == "EARLY_DISCOUNT"
                ),
                None,
            )
            if not allowed or allowed["pay_on"] != payload["requested_execution_date"]:
                raise ProblemError(
                    "VALIDATION_FAILED", "Early execution requires the deterministic scheduler"
                )
        account = vendor.bank_account
        now = timezone.now()
        policy = load_policy()
        registry = registry_snapshot(account)
        new = payee_new_or_changed(registry, now, policy.new_or_changed_window_days)
        network = NetworkResult(status="SKIP")
        if new and account:
            from apps.intel_client.service import get_network_result  # noqa: PLC0415

            network = get_network_result(tenant_id, account.iban)
        history = payment_history(tenant_id, payment.id)
        window = now - timedelta(hours=24)
        counted = [row for row in history if row.state in COUNTED_STATES and row.created_at >= window]
        accounts = {row.id: row for row in BankAccount.objects.filter(tenant_id=tenant_id)}
        new_total = sum(
            row.amount_minor
            for row in counted
            if row.account_id in accounts
            and payee_new_or_changed(registry_snapshot(accounts[row.account_id]), now, 30)
        )
        projected = forecast(tenant_id)
        known = []
        for other in Vendor.objects.filter(tenant_id=tenant_id).exclude(id=vendor.id):
            other_account = other.bank_account
            known.append(
                KnownVendor(
                    vendor_id=other.id,
                    name=other.name,
                    accounts=(
                        (KnownAccount(account_id=other_account.id, iban=other_account.iban),)
                        if other_account
                        else ()
                    ),
                )
            )
        inputs = GuardianInputs(
            tenant_id=tenant_id,
            proposal=ProposalInput(
                payment_id=payment.id,
                invoice_id=invoice.id,
                vendor_id=vendor.id,
                account_id=uuid.UUID(payload["account_id"]),
                amount_minor=payload["amount_minor"],
                currency=payload["currency"],
                reference=payload["reference"],
                due_date=extracted.due_date,
                invoice_fingerprint=invoice_fingerprint(
                    vendor.id, payload["reference"], payload["amount_minor"], payload["currency"]
                ),
            ),
            vendor=VendorSnapshot(vendor_id=vendor.id, name=vendor.name, blocked=vendor.blocked),
            registry_account=registry,
            known_vendors=tuple(known),
            extraction=InvoiceExtraction(
                vendor_name=extracted.vendor_name,
                reference=extracted.reference,
                amount_minor=extracted.amount_minor,
                currency=extracted.currency,
                bank_details_on_invoice=extracted.bank_details_on_invoice,
                untrusted_text=extracted.untrusted_text,
            ),
            history=history,
            rolling=RollingTotals(
                outflow_24h_minor=sum(row.amount_minor for row in counted),
                new_payee_outflow_24h_minor=new_total,
            ),
            forecast=ForecastBuffer(
                buffer_minor=projected["buffer_minor"],
                projected_min_balance_minor=min(row["closing_minor"] for row in projected["weeks"]),
            ),
            network=network,
            tenant_frozen=limits.frozen,
            now=now,
        )
        issued = issue_decision(inputs, policy, default_keystore())
        record_decision(issued)
        decision = issued.decision
        payment = transition(
            payment,
            "PROPOSED",
            "proposal_received",
            actor,
            TransitionContext(
                proposal=ProposalBinding(
                    vendor.id, uuid.UUID(payload["account_id"]), payload["amount_minor"], payload["currency"]
                )
            ),
        )
        if payload.get("requested_execution_date"):
            payment.requested_execution_date = payload["requested_execution_date"]
            payment.save(update_fields=["requested_execution_date"])
        cooloff = now + timedelta(seconds=600 if settings.SAFEPAY_MODE == "demo" else 14400)
        warnings = tuple(
            reason.code for reason in decision.reasons if reason.code in policy.warnings_requiring_ack
        )
        binding = DecisionBinding(
            uuid.UUID(decision.decision_id),
            decision.tier,
            cooloff if decision.tier == "T2" else None,
            (2 if payload["amount_minor"] >= policy.two_approver_threshold else 1)
            if decision.verdict == "HOLD"
            else 0,
            warnings,
        )
        state, trigger = {
            "DENY": ("DENIED", "decision_deny"),
            "HOLD": ("HELD", "decision_hold"),
            "ALLOW": ("CLEARED", "decision_allow_or_guarded"),
            "GUARDED": ("CLEARED", "decision_allow_or_guarded"),
        }[decision.verdict]
        transition(
            payment, state, trigger, Actor("guardian", "guardian"), TransitionContext(decision=binding)
        )
        if account and decision.verdict in ("ALLOW", "GUARDED") and account.first_used_at is None:
            account.first_used_at = now
            account.save(update_fields=["first_used_at"])
        return decision
