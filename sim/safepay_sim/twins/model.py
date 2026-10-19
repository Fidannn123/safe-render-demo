"""Typed in-memory model of a SYNTHETIC twin. Days are integers relative to START (day 0)."""

from __future__ import annotations

import datetime as dt
import random
import uuid
from dataclasses import dataclass, field
from typing import Literal

from .catalog import SectorSpec

START = dt.date(2026, 7, 1)
HISTORY_DAYS = 90
AS_OF_DAY = HISTORY_DAYS  # 2026-09-29: first day after the 90 simulated days
FORECAST_WEEKS = 13
CURRENCY = "USD"

VendorRole = Literal[
    "regular",
    "target",
    "changed_legit",
    "reported",
    "lookalike_account",
    "lookalike_registered",
    "new_split",
    "big",
]


def day_to_date(day: int) -> dt.date:
    return START + dt.timedelta(days=day)


def iso_day(day: int) -> str:
    return day_to_date(day).isoformat()


def new_uuid(rng: random.Random) -> str:
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


@dataclass
class Vendor:
    vendor_id: str
    name: str
    tax_id: str
    country: str
    iban: str
    expense_account: Literal["Expense", "COGS"]
    cadence_days: int
    terms_days: int
    base_minor: int
    criticality: int
    late_fee_minor: int
    role: VendorRole
    added_day: int
    account_changed_day: int | None = None
    previous_ibans: list[str] = field(default_factory=list)
    email_domain: str = ""
    ref_prefix: str = ""
    lookalike_of: str | None = None  # vendor_id this vendor/account imitates (attack setups)

    @property
    def has_history(self) -> bool:
        return self.role not in ("lookalike_registered", "new_split")


@dataclass
class Customer:
    customer_id: str
    name: str
    cadence_days: int
    terms_days: int
    on_time_pct: int
    base_minor: int = 0


@dataclass
class ApInvoice:
    invoice_id: str
    vendor_id: str
    reference: str
    issue_day: int
    due_day: int
    amount_minor: int
    lines: list[int]
    discount_bp: int | None = None
    discount_days: int | None = None
    paid_day: int | None = None
    payment_id: str | None = None
    paid_iban: str | None = None

    @property
    def is_open(self) -> bool:
        return self.paid_day is None


@dataclass
class ArInvoice:
    invoice_id: str
    customer_id: str
    reference: str
    issue_day: int
    due_day: int
    amount_minor: int
    paid_day: int | None
    end_to_end_id: str | None

    @property
    def is_open(self) -> bool:
        return self.paid_day is None


@dataclass
class OpexItem:
    payee: str
    amount_minor: int
    period_days: int
    first_day: int

    def days(self, until: int) -> list[int]:
        return list(range(self.first_day, until, self.period_days))


@dataclass
class OpexDebit:
    day: int
    payee: str
    amount_minor: int
    end_to_end_id: str


@dataclass
class Twin:
    spec: SectorSpec
    tenant_id: str
    bank_iban: str
    vendors: list[Vendor]
    customers: list[Customer]
    ap: list[ApInvoice]
    ar: list[ArInvoice]
    opex_items: list[OpexItem]
    opex_debits: list[OpexDebit]
    opening_minor: int = 0
    opening_e2e: str = ""

    @property
    def key(self) -> str:
        return self.spec.key

    def vendor(self, vendor_id: str) -> Vendor:
        for v in self.vendors:
            if v.vendor_id == vendor_id:
                return v
        raise KeyError(vendor_id)

    def vendors_by_role(self, role: VendorRole) -> list[Vendor]:
        return [v for v in self.vendors if v.role == role]

    def paid_ap(self, vendor_id: str | None = None) -> list[ApInvoice]:
        return [
            inv
            for inv in self.ap
            if inv.paid_day is not None and (vendor_id is None or inv.vendor_id == vendor_id)
        ]

    def first_used_day(self, vendor: Vendor) -> int | None:
        days = [inv.paid_day for inv in self.paid_ap(vendor.vendor_id) if inv.paid_iban == vendor.iban]
        return min(d for d in days if d is not None) if days else None

    def cash_flows(self) -> list[tuple[int, int]]:
        """(day, signed amount) bank movements of the 90-day history, excluding the opening balance."""
        flows: list[tuple[int, int]] = []
        for a in self.ar:
            if a.paid_day is not None:
                flows.append((a.paid_day, a.amount_minor))
        for p in self.ap:
            if p.paid_day is not None:
                flows.append((p.paid_day, -p.amount_minor))
        for o in self.opex_debits:
            flows.append((o.day, -o.amount_minor))
        return flows

    def balance_path_relative(self) -> list[int]:
        """End-of-day bank balance for days 0..89 with opening 0."""
        per_day = [0] * HISTORY_DAYS
        for day, amt in self.cash_flows():
            per_day[day] += amt
        out: list[int] = []
        running = 0
        for d in per_day:
            running += d
            out.append(running)
        return out

    def closing_balance(self) -> int:
        return self.opening_minor + self.balance_path_relative()[-1]
