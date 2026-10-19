"""Load one SYNTHETIC digital twin (sim/twins output) into core. This is the documented seed bypass (D-008).

Vendors get verified registry accounts directly, and 90 days of history are replayed from the twin's
ledger.json through the spec posting templates, with timestamps pinned to their historical dates. Request
handling never uses this module. Every company, account and amount here is invented by safepay_sim.twins.
"""

from __future__ import annotations

import datetime as dt
import json
import secrets
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.audit.chain import append_event
from apps.cashflow.models import Receivable, RecurringOpex
from apps.invoices.models import Invoice
from apps.ledger.models import JournalEntry
from apps.ledger.service import balance_of, ensure_tenant_accounts, post_template
from apps.ledger.tenancy import tenant_transaction
from apps.payments.fingerprint import invoice_fingerprint
from apps.payments.models import Payment
from apps.payments.service import lock_tenant_limits
from apps.tenants.models import Tenant
from apps.vendors.models import BankAccount, Vendor, encrypt_iban

from .clock import SimClock, sim_clock

CRITICALITY = {1: "LOW", 2: "MEDIUM", 3: "HIGH"}
SEED_ACTOR = {"type": "system", "id": "synthetic-seed"}
ROLES = ("owner", "approver", "accountant")


@dataclass(frozen=True)
class DayMap:
    """Simulated calendar -> real calendar. The twin's as_of date lands on `real_as_of`."""

    sim_as_of: dt.date
    real_as_of: dt.date

    @property
    def offset(self) -> dt.timedelta:
        return self.real_as_of - self.sim_as_of

    def date(self, value: str | dt.date) -> dt.date:
        day = dt.date.fromisoformat(value) if isinstance(value, str) else value
        return day + self.offset

    def at(self, value: str | dt.date, hour: int = 12, minute: int = 0) -> dt.datetime:
        return dt.datetime.combine(self.date(value), dt.time(hour, minute), tzinfo=dt.UTC)

    def instant(self, sim_time: str) -> dt.datetime:
        """An RFC 3339 simulated timestamp shifted onto the real calendar."""
        value = dt.datetime.fromisoformat(sim_time.replace("Z", "+00:00"))
        return value + self.offset


def legacy_account_id(tenant_id: uuid.UUID) -> uuid.UUID:
    """A second SIMULATED bank account of the member (history only; core does not reconcile it)."""
    return uuid.uuid5(tenant_id, "legacy-account")


def iban(country: str, bban: str) -> str:
    """ISO 13616 check digits (mod 97) for a synthetic BBAN."""
    digits = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def synthetic_iban(rng: secrets.SystemRandom | None = None) -> str:
    """A fresh GB-format IBAN for a simulated SME bank account (unique per seeding)."""
    rng = rng or secrets.SystemRandom()
    return iban("GB", "SAFE" + "".join(str(rng.randrange(10)) for _ in range(14)))


@dataclass
class SeedUser:
    username: str
    password: str
    role: str
    user_id: str
    totp_key_hex: str


@dataclass
class SeedResult:
    twin_key: str
    tenant_id: uuid.UUID
    bank_account_id: uuid.UUID
    bank_iban: str
    company: str
    closing_balance_minor: int
    users: list[SeedUser] = field(default_factory=list)
    vendors: dict[str, uuid.UUID] = field(default_factory=dict)  # sim vendor_id -> core vendor id
    accounts: dict[str, uuid.UUID] = field(default_factory=dict)  # IBAN -> core registry account id
    open_payments: dict[str, uuid.UUID] = field(default_factory=dict)  # sim invoice_id -> payment id

    def as_dict(self) -> dict[str, Any]:
        return {
            "twin_key": self.twin_key,
            "tenant_id": str(self.tenant_id),
            "bank_account_id": str(self.bank_account_id),
            "bank_iban": self.bank_iban,
            "company": self.company,
            "closing_balance_minor": self.closing_balance_minor,
            "users": [vars(user) for user in self.users],
            "vendors": {key: str(value) for key, value in self.vendors.items()},
            "open_payments": {key: str(value) for key, value in self.open_payments.items()},
            "synthetic": True,
        }


def _derive(tenant_id: uuid.UUID, value: str) -> uuid.UUID:
    """Twin ids are reused per configuration, so every primary key is namespaced by the tenant."""
    return uuid.uuid5(tenant_id, value)


def _create_users(tenant_id: uuid.UUID, prefix: str) -> list[SeedUser]:
    users = []
    for role in ROLES:
        password = secrets.token_urlsafe(18)
        user = User.objects.create_user(
            username=f"{prefix}.{role}", password=password, tenant_id=tenant_id, role=role
        )
        key = secrets.token_hex(20)
        TOTPDevice.objects.create(user=user, name=f"{role} authenticator (synthetic)", key=key, confirmed=True)
        users.append(SeedUser(user.username, password, role, str(user.id), key))
    return users


def _vendors(tenant_id: uuid.UUID, twin: dict[str, Any], days: DayMap, clock: SimClock, result: SeedResult) -> None:
    for spec in twin["vendors"]:
        clock.set(days.at(spec["added_on"], 9))
        vendor = Vendor.objects.create(
            id=_derive(tenant_id, spec["vendor_id"]),
            tenant_id=tenant_id,
            name=spec["name"],
            tax_id=spec.get("tax_id") or "",
            expense_account=spec["expense_account"],
            criticality=CRITICALITY[int(spec["criticality"])],
            # The twin prints a flat late fee; core ranks deferrals by basis points of the amount.
            late_fee_bp=min(10000, int(spec["late_fee_minor"]) // 10),
        )
        result.vendors[spec["vendor_id"]] = vendor.id
        added = days.at(spec["added_on"], 9)
        changed = days.at(spec["account_changed_on"], 9) if spec["account_changed_on"] else added
        for previous in spec["previous_accounts"]:
            old = BankAccount(
                id=_derive(tenant_id, f"account:{previous['iban']}"),
                tenant_id=tenant_id,
                vendor=vendor,
                country=previous["country"],
                holder_name=vendor.name,
                active=False,
                verified_at=added,
                changed_at=added,
                first_used_at=added,
            )
            old.iban = previous["iban"]
            old.save(force_insert=True)
            result.accounts[previous["iban"]] = old.id
        registry = spec["registry_account"]
        first_used = spec["account_first_used_on"]
        current = BankAccount(
            id=_derive(tenant_id, f"account:{registry['iban']}"),
            tenant_id=tenant_id,
            vendor=vendor,
            country=registry["country"],
            holder_name=vendor.name,
            active=True,
            verified_at=changed,
            changed_at=changed,
            first_used_at=days.at(first_used, 9) if first_used else None,
        )
        current.iban = registry["iban"]
        current.save(force_insert=True)
        result.accounts[registry["iban"]] = current.id


def _cashflow_inputs(
    tenant_id: uuid.UUID, twin: dict[str, Any], ledger: dict[str, Any], days: DayMap
) -> None:
    for row in ledger["ar_invoices"]:
        Receivable.objects.create(
            id=_derive(tenant_id, row["invoice_id"]),
            tenant_id=tenant_id,
            customer_id=uuid.UUID(row["customer_id"]),
            amount_minor=row["amount_minor"],
            currency=row["currency"],
            issued_on=days.date(row["issue_date"]),
            due_on=days.date(row["due_date"]),
            paid_on=days.date(row["paid_date"]) if row["paid_date"] else None,
        )
    for opex in twin["recurring_opex"]:
        due = dt.date.fromisoformat(opex["first_date"])
        while due < days.sim_as_of:
            due += dt.timedelta(days=int(opex["period_days"]))
        RecurringOpex.objects.create(
            tenant_id=tenant_id,
            name=opex["payee"][:140],
            amount_minor=opex["amount_minor"],
            currency=twin["currency"],
            next_due=days.date(due),
            interval_days=int(opex["period_days"]),
        )


def _accept_invoice(
    tenant_id: uuid.UUID,
    event: dict[str, Any],
    invoice: dict[str, Any],
    vendor: Vendor,
    payment_id: uuid.UUID,
    days: DayMap,
    clock: SimClock,
) -> None:
    extracted: dict[str, Any] = {
        "invoice_id": str(_derive(tenant_id, invoice["invoice_id"])),
        "vendor_name": vendor.name,
        "reference": invoice["reference"],
        "amount_minor": invoice["amount_minor"],
        "currency": invoice["currency"],
        "due_date": days.date(invoice["due_date"]).isoformat(),
        "issue_date": days.date(invoice["issue_date"]).isoformat(),
        "extractor": {"provider": "synthetic-seed", "model": "none", "prompt_sha256": "0" * 64},
    }
    if invoice["early_payment_discount"]:
        extracted["early_payment_discount"] = invoice["early_payment_discount"]
    source = {"synthetic_history": True, "reference": invoice["reference"]}
    Invoice.objects.create(
        id=_derive(tenant_id, invoice["invoice_id"]),
        tenant_id=tenant_id,
        source={"ciphertext": encrypt_iban(json.dumps(source), tenant_id)},
        extracted=extracted,
        received_at=clock.now(),
    )
    payment = Payment.objects.create(
        id=payment_id,
        tenant_id=tenant_id,
        invoice_id=_derive(tenant_id, invoice["invoice_id"]),
        state="EXTRACTED",
        vendor_id=vendor.id,
        amount_minor=invoice["amount_minor"],
        currency=invoice["currency"],
        reference=invoice["reference"],
        due_date=dt.date.fromisoformat(extracted["due_date"]),
        expense_account=vendor.expense_account,
        invoice_fingerprint=invoice_fingerprint(
            vendor.id, invoice["reference"], invoice["amount_minor"], invoice["currency"]
        ),
        initiated_by=SEED_ACTOR,
    )
    entry = post_template(
        tenant_id,
        "invoice_accepted",
        event["amount_minor"],
        event["currency"],
        vendor_id=vendor.id,
        expense_account=vendor.expense_account,
        payment_id=payment.id,
        memo="synthetic history",
    )
    payment.invoice_accrued, payment.accrual_entry_id = True, entry.id
    payment.save(update_fields=["invoice_accrued", "accrual_entry_id"])


def _replay(
    tenant_id: uuid.UUID,
    ledger: dict[str, Any],
    days: DayMap,
    clock: SimClock,
    result: SeedResult,
) -> None:
    invoices = {row["invoice_id"]: row for row in ledger["ap_invoices"]}
    paid_e2e = {
        event["invoice_id"]: event["end_to_end_id"]
        for event in ledger["events"]
        if event["templates"] == ["reserve", "submit", "settle"]
    }

    def payment_id(invoice_id: str) -> uuid.UUID:
        key = paid_e2e.get(invoice_id)
        return _derive(tenant_id, f"e2e:{key}" if key else f"payment:{invoice_id}")

    for event in sorted(ledger["events"], key=lambda row: row["seq"]):
        clock.set(days.at(event["date"], 12) + dt.timedelta(seconds=int(event["seq"])))
        templates, amount, currency = event["templates"], event["amount_minor"], event["currency"]
        if templates == ["invoice_accepted"]:
            vendor = Vendor.objects.get(tenant_id=tenant_id, id=result.vendors[event["vendor_id"]])
            invoice = invoices[event["invoice_id"]]
            pid = payment_id(event["invoice_id"])
            _accept_invoice(tenant_id, event, invoice, vendor, pid, days, clock)
            if invoice["status"] == "OPEN":
                result.open_payments[event["invoice_id"]] = pid
        elif templates == ["reserve", "submit", "settle"]:
            payment = Payment.objects.get(tenant_id=tenant_id, id=payment_id(event["invoice_id"]))
            for template in templates:
                post_template(
                    tenant_id,
                    template,
                    amount,
                    currency,
                    vendor_id=payment.vendor_id,
                    payment_id=payment.id,
                    memo="synthetic history",
                )
            payment.state, payment.tier = "RECONCILED", "T1"
            payment.account_id = result.accounts[event["creditor_iban"]]
            payment.save()
        elif len(templates) == 1 and templates[0] in (
            "opening_balance",
            "ar_invoice_issued",
            "customer_receipt",
            "direct_debit_opex",
        ):
            customer = event.get("customer_id")
            post_template(
                tenant_id,
                templates[0],
                amount,
                currency,
                customer_id=uuid.UUID(customer) if customer else None,
                memo="synthetic history",
            )
        else:  # pragma: no cover - the twin format is fixed by sim/twins
            raise ValueError(f"unknown twin ledger event {templates}")


def seed_twin(
    twin: dict[str, Any],
    ledger: dict[str, Any],
    *,
    tenant_id: uuid.UUID,
    days: DayMap,
    username_prefix: str,
    bank_account_id: uuid.UUID | None = None,
    bank_iban: str | None = None,
    label: str = "",
) -> SeedResult:
    """Create the tenant, users, registry and history of one twin. All-or-nothing."""
    result = SeedResult(
        twin_key=twin["twin_key"],
        tenant_id=tenant_id,
        bank_account_id=bank_account_id or _derive(tenant_id, "bank-account"),
        bank_iban=bank_iban or synthetic_iban(),
        company=twin["company"],
        closing_balance_minor=int(ledger["closing_bank_balance_minor"]),
    )
    clock = SimClock(days.at(twin["history"]["start_date"], 0))
    with sim_clock(clock), transaction.atomic():
        Tenant.objects.create(
            id=tenant_id,
            name=f"{twin['company']}{label} (SYNTHETIC)"[:140],
            sector=twin["sector"][:64],
            bank_account_id=result.bank_account_id,
        )
        result.users = _create_users(tenant_id, username_prefix)
        with tenant_transaction(tenant_id):
            lock_tenant_limits(tenant_id)
            ensure_tenant_accounts(tenant_id)
            _vendors(tenant_id, twin, days, clock, result)
            _cashflow_inputs(tenant_id, twin, ledger, days)
            _replay(tenant_id, ledger, days, clock, result)
            available = balance_of(tenant_id, "Bank:available", twin["currency"])
            if available != result.closing_balance_minor:
                raise ValueError(
                    f"{twin['twin_key']}: replayed balance {available} != twin {result.closing_balance_minor}"
                )
            clock.set(days.at(days.sim_as_of, 6))
            append_event(
                tenant_id=tenant_id,
                actor=SEED_ACTOR,
                action="SYNTHETIC_SEED",
                subject_type="tenant",
                subject_id=str(tenant_id),
                data={
                    "twin_key": twin["twin_key"],
                    "vendors": len(result.vendors),
                    "journal_entries": JournalEntry.objects.filter(tenant_id=tenant_id).count(),
                    "synthetic": True,
                },
            )
    return result
