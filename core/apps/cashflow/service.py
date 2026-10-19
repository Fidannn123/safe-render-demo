"""Integer-money 13-week forecast, traceable analysis, scheduling and synthetic financing matching."""

from __future__ import annotations

import statistics
import uuid
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction
from typing import Any

from django.db.models import Sum
from django.utils import timezone

from apps.ledger.models import Posting
from apps.ledger.service import balance_of
from apps.payments.errors import ProblemError
from apps.payments.models import Payment
from apps.vendors.models import Vendor

from .models import Receivable, RecurringOpex

OPEN_STATES = ("EXTRACTED", "PROPOSED", "HELD", "REJECTED")


@dataclass(frozen=True)
class CashEvent:
    id: str
    kind: str
    amount_minor: int
    on: date


def project(
    as_of: date, opening_minor: int, buffer_minor: int, events: list[CashEvent], currency: str = "USD"
) -> dict[str, Any]:
    """Pure, reproducible weekly projection. Positive events are inflows; negatives are outflows."""
    inflows, outflows = [0] * 13, [0] * 13
    sources: list[dict[str, Any]] = [
        {
            "kind": "bank_balance",
            "id": "Bank:available",
            "amount_minor": opening_minor,
            "date": as_of.isoformat(),
        }
    ]
    for event in sorted(events, key=lambda row: (row.on, row.kind, row.id)):
        index = max(0, (event.on - as_of).days // 7)
        if index >= 13:
            continue
        if event.amount_minor >= 0:
            inflows[index] += event.amount_minor
        else:
            outflows[index] -= event.amount_minor
        sources.append(
            {
                "kind": event.kind,
                "id": event.id,
                "amount_minor": event.amount_minor,
                "date": event.on.isoformat(),
            }
        )
    running = opening_minor
    weeks = []
    for index in range(13):
        closing = running + inflows[index] - outflows[index]
        status = "SHORTFALL" if closing < 0 else "LOW_BUFFER" if closing < buffer_minor else "OK"
        weeks.append(
            {
                "week_index": index,
                "week_start": (as_of + timedelta(days=index * 7)).isoformat(),
                "opening_minor": running,
                "inflows_minor": inflows[index],
                "outflows_minor": outflows[index],
                "closing_minor": closing,
                "status": status,
            }
        )
        running = closing
    return {
        "as_of": as_of.isoformat(),
        "currency": currency,
        "opening_balance_minor": opening_minor,
        "buffer_minor": buffer_minor,
        "weeks": weeks,
        "sources": sources,
    }


def customer_behaviour(rows: list[Receivable], customer_id: uuid.UUID) -> tuple[Fraction, int]:
    paid = [row for row in rows if row.customer_id == customer_id and row.paid_on is not None]
    if not paid:
        return Fraction(1), 0
    delays = [max(0, (row.paid_on - row.due_on).days) for row in paid if row.paid_on is not None]
    on_time = sum(row.paid_on <= row.due_on for row in paid if row.paid_on is not None)
    return Fraction(on_time, len(paid)), int(statistics.median_low(delays))


def forecast(tenant_id: uuid.UUID, as_of: date | None = None, currency: str = "USD") -> dict[str, Any]:
    as_of = as_of or timezone.localdate()
    events: list[CashEvent] = []
    receivables = list(Receivable.objects.filter(tenant_id=tenant_id, currency=currency))
    for row in receivables:
        if row.paid_on is None:
            rate, delay = customer_behaviour(receivables, row.customer_id)
            events.append(
                CashEvent(
                    str(row.id),
                    "ar_invoice",
                    round(row.amount_minor * rate),
                    max(as_of, row.due_on + timedelta(days=delay)),
                )
            )
    for payment in Payment.objects.filter(tenant_id=tenant_id, currency=currency, state__in=OPEN_STATES):
        if payment.amount_minor is not None:
            events.append(
                CashEvent(str(payment.id), "payment", -payment.amount_minor, payment.due_date or as_of)
            )
    for opex in RecurringOpex.objects.filter(tenant_id=tenant_id, currency=currency):
        on = opex.next_due
        while on < as_of:
            on += timedelta(days=opex.interval_days)
        while on < as_of + timedelta(days=91):
            events.append(CashEvent(str(opex.id), "recurring_opex", -opex.amount_minor, on))
            on += timedelta(days=opex.interval_days)
    historical = list(
        Posting.objects.filter(
            tenant_id=tenant_id,
            currency=currency,
            entry__template="direct_debit_opex",
            account__code="Expense",
            entry__created_at__date__gte=as_of - timedelta(days=84),
            entry__created_at__date__lt=as_of,
        ).select_related("entry")
    )
    weekly = [
        sum(
            p.amount_minor
            for p in historical
            if as_of - timedelta(days=7 * (index + 1))
            <= p.entry.created_at.date()
            < as_of - timedelta(days=7 * index)
        )
        for index in range(12)
    ]
    buffer_minor = max(500000, 2 * round(Fraction(sum(sorted(weekly)[5:7]), 2)))
    return project(as_of, balance_of(tenant_id, "Bank:available", currency), buffer_minor, events, currency)


def alerts(tenant_id: uuid.UUID, as_of: date | None = None) -> list[dict[str, Any]]:
    result = forecast(tenant_id, as_of)
    output = []
    for week in result["weeks"]:
        if week["status"] == "OK":
            continue
        status = week["status"]
        output.append(
            {
                "alert_id": str(uuid.uuid5(tenant_id, f"{week['week_start']}:{status}")),
                "type": status,
                "severity": "CRITICAL" if status == "SHORTFALL" else "WARN",
                "week_start": week["week_start"],
                "amount_minor": week["closing_minor"],
                "created_at": timezone.now().isoformat(),
                "status": "OPEN",
                "detail": f"Projected closing balance {week['closing_minor']} USD minor units; "
                f"buffer {result['buffer_minor']} (synthetic forecast).",
            }
        )
    return output


def analysis(
    tenant_id: uuid.UUID, metric: str, period_from: date | None = None, period_to: date | None = None
) -> dict[str, Any]:
    if metric not in {"DPO", "DSO", "BURN", "TOP_VENDORS"}:
        raise ProblemError("VALIDATION_FAILED", "Unknown analysis metric")
    end = period_to or timezone.localdate()
    start = period_from or end - timedelta(days=89)
    if start > end:
        raise ProblemError("VALIDATION_FAILED", "period_from must not follow period_to")
    days = (end - start).days + 1
    sources = []
    items: list[dict[str, Any]] = []
    value: str | None = "0.0"
    unit = "days"
    if metric == "DSO":
        rows = list(Receivable.objects.filter(tenant_id=tenant_id, issued_on__range=(start, end)))
        total = sum(row.amount_minor for row in rows)
        outstanding = sum(row.amount_minor for row in rows if row.paid_on is None or row.paid_on > end)
        value = str((Decimal(outstanding * days) / Decimal(total or 1)).quantize(Decimal("0.1")))
        sources = [
            {
                "kind": "ar_invoice",
                "id": str(row.id),
                "amount_minor": row.amount_minor,
                "date": row.issued_on.isoformat(),
            }
            for row in rows
        ]
    elif metric == "DPO":
        rows_p = list(
            Payment.objects.filter(
                tenant_id=tenant_id, created_at__date__range=(start, end), invoice_accrued=True
            )
        )
        total = sum(row.amount_minor or 0 for row in rows_p)
        outstanding = sum(
            row.amount_minor or 0 for row in rows_p if row.state not in ("SETTLED", "RECONCILED")
        )
        value = str((Decimal(outstanding * days) / Decimal(total or 1)).quantize(Decimal("0.1")))
        sources = [
            {
                "kind": "payment",
                "id": str(row.id),
                "amount_minor": row.amount_minor,
                "date": row.created_at.date().isoformat(),
            }
            for row in rows_p
        ]
    else:
        postings = list(
            Posting.objects.filter(
                tenant_id=tenant_id,
                entry__created_at__date__range=(start, end),
                account__code="Bank:available",
                entry__template__in=(
                    "reserve",
                    "direct_debit_opex",
                    "release_reserved",
                    "release_in_transit",
                ),
            ).select_related("entry")
        )
        sources = [
            {
                "kind": "journal_entry",
                "id": str(row.entry_id),
                "amount_minor": row.amount_minor,
                "date": row.entry.created_at.date().isoformat(),
            }
            for row in postings
        ]
        if metric == "BURN":
            value = str(round(Fraction(-sum(row.amount_minor for row in postings) * 30, days)))
            unit = "minor_per_30_days"
        else:
            paid = Payment.objects.filter(
                tenant_id=tenant_id, state__in=("SETTLED", "RECONCILED"), updated_at__date__range=(start, end)
            )
            totals = (
                paid.values("vendor_id").annotate(total=Sum("amount_minor")).order_by("-total", "vendor_id")
            )
            names = {row.id: row.name for row in Vendor.objects.filter(tenant_id=tenant_id)}
            items = [
                {
                    "vendor_id": str(row["vendor_id"]),
                    "name": names.get(row["vendor_id"], "Unknown"),
                    "amount_minor": row["total"],
                }
                for row in totals[:50]
            ]
            value, unit = None, "list"
    return {
        "metric": metric,
        "period_from": start.isoformat(),
        "period_to": end.isoformat(),
        "value": value,
        "unit": unit,
        "currency": "USD",
        "items": items,
        "source_rows": sources,
    }


def schedule(tenant_id: uuid.UUID, as_of: date | None = None) -> dict[str, Any]:
    as_of = as_of or timezone.localdate()
    projected = forecast(tenant_id, as_of)
    breach = any(week["status"] != "OK" for week in projected["weeks"])
    shortage = any(week["status"] == "SHORTFALL" for week in projected["weeks"])
    vendors = {row.id: row for row in Vendor.objects.filter(tenant_id=tenant_id)}
    from apps.invoices.models import Invoice  # noqa: PLC0415

    extracted = {row.id: row.extracted for row in Invoice.objects.filter(tenant_id=tenant_id)}
    items = []
    for payment in Payment.objects.filter(tenant_id=tenant_id, state__in=OPEN_STATES).order_by(
        "due_date", "id"
    ):
        if payment.vendor_id is None or payment.amount_minor is None:
            continue
        vendor = vendors[payment.vendor_id]
        due = payment.due_date or as_of
        pay_on, reason, apr = max(as_of, due), "DUE_DATE", None
        data = extracted.get(payment.invoice_id) or {}
        discount = data.get("early_payment_discount")
        if discount and data.get("issue_date"):
            issued = date.fromisoformat(data["issue_date"])
            last = issued + timedelta(days=discount["within_days"])
            gap = (due - last).days
            bp = discount["discount_bp"]
            if gap > 0 and bp < 10000:
                rate = Decimal(bp) / Decimal(10000 - bp) * Decimal(365) / Decimal(gap)
                apr = str(rate.quantize(Decimal("0.0001")))
                if rate > Decimal("0.12") and not breach and last >= as_of:
                    pay_on, reason = last, "EARLY_DISCOUNT"
        if shortage and vendor.criticality != "HIGH":
            pay_on, reason = max(as_of, due) + timedelta(days=7), "DEFERRED_SHORTFALL"
        items.append(
            {
                "invoice_id": str(payment.invoice_id),
                "payment_id": str(payment.id),
                "vendor_id": str(payment.vendor_id),
                "amount_minor": payment.amount_minor,
                "due_date": due.isoformat(),
                "pay_on": pay_on.isoformat(),
                "reason": reason,
                "discount_apr": apr,
                "late_fee_minor": payment.amount_minor * vendor.late_fee_bp // 10000,
            }
        )
    if shortage:
        items.sort(
            key=lambda row: (
                row["late_fee_minor"],
                {"LOW": 0, "MEDIUM": 1, "HIGH": 2}[vendors[uuid.UUID(str(row["vendor_id"]))].criticality],
                row["due_date"],
                row["invoice_id"],
            )
        )
    return {"as_of": as_of.isoformat(), "currency": "USD", "items": items, "buffer_breach": breach}


def financing(
    shortfall_minor: int,
    currency: str,
    needed_by: date,
    *,
    receivables_minor: int = 0,
    history_days: int = 90,
    max_term_days: int = 1825,
    as_of: date | None = None,
) -> dict[str, Any]:
    if shortfall_minor <= 0 or currency != "USD":
        raise ProblemError("VALIDATION_FAILED", "Positive USD shortfall required")
    as_of = as_of or timezone.localdate()
    products = (
        ("synthetic-overdraft", "OVERDRAFT", 10000, 3000000, 30, "0.1400", 2500, 30, 0),
        ("synthetic-invoice", "INVOICE_FINANCING", 100000, 10000000, 60, "0.0900", 10000, 60, 1),
        ("synthetic-loan", "SHORT_TERM_LOAN", 250000, 20000000, 180, "0.1100", 20000, 90, 2),
    )
    output = []
    for product, kind, low, high, term, apr, fees, minimum_history, lead_days in products:
        reasons = []
        if shortfall_minor < low:
            reasons.append("AMOUNT_TOO_LOW")
        if shortfall_minor > high:
            reasons.append("AMOUNT_TOO_HIGH")
        if term > max_term_days:
            reasons.append("TERM_TOO_LONG")
        if history_days < minimum_history:
            reasons.append("MIN_HISTORY")
        if kind == "INVOICE_FINANCING" and receivables_minor < shortfall_minor:
            reasons.append("NO_RECEIVABLES")
        if needed_by < as_of + timedelta(days=lead_days):
            reasons.append("TERM_TOO_SHORT")
        interest = Decimal(shortfall_minor) * Decimal(apr) * Decimal(term) / Decimal(365)
        cost = int(interest.quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)) + fees
        output.append(
            {
                "product_id": product,
                "provider_name": "SafePay Synthetic Finance",
                "type": kind,
                "amount_minor": shortfall_minor,
                "term_days": term,
                "apr": apr,
                "fees_minor": fees,
                "total_cost_minor": cost,
                "eligible": not reasons,
                "ineligibility_reasons": reasons,
                "rank": None,
            }
        )
    output.sort(key=lambda row: (not row["eligible"], row["total_cost_minor"], row["product_id"]))
    rank = 1
    for option in output:
        if option["eligible"]:
            option["rank"] = rank
            rank += 1
    return {"ranked_by": "total_cost_minor", "synthetic_catalog": True, "options": output}
