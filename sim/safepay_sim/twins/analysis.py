"""Independent oracles: financial analysis (DPO, DSO, burn, top vendors), payables schedule, financing need.

Definitions (window = the 90 simulated days, as_of = day 90):
  * DPO = open AP at as_of / AP invoiced in the window x 90 days, 2 decimals, ROUND_HALF_EVEN.
  * DSO = open AR at as_of / AR invoiced in the window x 90 days, 2 decimals, ROUND_HALF_EVEN.
  * gross weekly burn = cash outflows (vendor payments + direct-debit opex) in the 84 days before
    as_of / 12, rounded half-even to minor units; net weekly burn = (outflows - customer receipts) / 12.
  * top vendors = registry vendors ranked by settled payments in the window (desc), ties by name; top 3.
  * schedule: each open payable is paid on its due date, or early on the last discount day when the
    discount APR = (d / (1 - d)) x (365 / (due_days - discount_days)) is strictly above the hurdle
    and the twin has no LOW_BUFFER/SHORTFALL week (early payment must not deepen a breach).
  * deferral order (SHORTFALL twins): open payables of criticality < 3, by late fee asc, then
    criticality asc, then due date, then reference.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any

from .forecast import Forecast
from .model import AS_OF_DAY, HISTORY_DAYS, Twin, iso_day
from .policy import Policy
from .textutil import decimal_str


def analysis_oracle(twin: Twin) -> dict[str, Any]:
    purchases = sum(p.amount_minor for p in twin.ap if p.issue_day < HISTORY_DAYS)
    ap_open = sum(p.amount_minor for p in twin.ap if p.is_open)
    sales = sum(a.amount_minor for a in twin.ar)
    ar_open = sum(a.amount_minor for a in twin.ar if a.is_open)
    lo = AS_OF_DAY - 84
    outflows = sum(p.amount_minor for p in twin.ap if p.paid_day is not None and p.paid_day >= lo)
    outflows += sum(d.amount_minor for d in twin.opex_debits if d.day >= lo)
    inflows = sum(a.amount_minor for a in twin.ar if a.paid_day is not None and a.paid_day >= lo)
    paid_by_vendor: dict[str, int] = {}
    for p in twin.ap:
        if p.paid_day is not None:
            paid_by_vendor[p.vendor_id] = paid_by_vendor.get(p.vendor_id, 0) + p.amount_minor
    ranked = sorted(paid_by_vendor.items(), key=lambda kv: (-kv[1], twin.vendor(kv[0]).name))
    return {
        "window": {"start_date": iso_day(0), "end_date": iso_day(HISTORY_DAYS - 1), "days": HISTORY_DAYS},
        "dpo_days": decimal_str(Fraction(ap_open * HISTORY_DAYS, purchases), 2),
        "dpo_inputs": {"ap_open_minor": ap_open, "ap_invoiced_minor": purchases},
        "dso_days": decimal_str(Fraction(ar_open * HISTORY_DAYS, sales), 2),
        "dso_inputs": {"ar_open_minor": ar_open, "ar_invoiced_minor": sales},
        "gross_burn_weekly_minor": round(Fraction(outflows, 12)),
        "net_burn_weekly_minor": round(Fraction(outflows - inflows, 12)),
        "burn_inputs": {
            "start_date": iso_day(lo),
            "weeks": 12,
            "outflows_minor": outflows,
            "inflows_minor": inflows,
        },
        "top_vendors": [
            {"vendor_id": vid, "name": twin.vendor(vid).name, "paid_minor": amt} for vid, amt in ranked[:3]
        ],
        "questions": [
            {"id": "dpo", "text": "What is our DPO over the last 90 days?", "answer_key": "dpo_days"},
            {"id": "dso", "text": "What is our DSO over the last 90 days?", "answer_key": "dso_days"},
            {"id": "burn", "text": "What is our weekly gross burn?", "answer_key": "gross_burn_weekly_minor"},
            {
                "id": "top_vendors",
                "text": "Who are our top 3 vendors by amount paid?",
                "answer_key": "top_vendors",
            },
        ],
    }


def discount_apr(bp: int, due_days: int, discount_days: int, day_count: int) -> Fraction:
    d = Fraction(bp, 10000)
    return (d / (1 - d)) * Fraction(day_count, due_days - discount_days)


def schedule_oracle(twin: Twin, forecast: Forecast, policy: Policy) -> dict[str, Any]:
    stressed = bool(forecast.weeks_with("LOW_BUFFER") or forecast.weeks_with("SHORTFALL"))
    items: list[dict[str, Any]] = []
    captured = 0
    for p in sorted((p for p in twin.ap if p.is_open), key=lambda p: (p.due_day, p.reference)):
        item: dict[str, Any] = {
            "invoice_id": p.invoice_id,
            "vendor_id": p.vendor_id,
            "reference": p.reference,
            "amount_minor": p.amount_minor,
            "due_date": iso_day(p.due_day),
            "early_payment_discount": None,
            "apr": None,
            "action": "PAY_ON_DUE",
            "pay_date": iso_day(p.due_day),
            "pay_amount_minor": p.amount_minor,
            "discount_captured_minor": 0,
        }
        if p.discount_bp is not None and p.discount_days is not None:
            apr = discount_apr(p.discount_bp, p.due_day - p.issue_day, p.discount_days, policy.apr_day_count)
            item["early_payment_discount"] = {"discount_bp": p.discount_bp, "within_days": p.discount_days}
            item["apr"] = decimal_str(apr, 6)
            if apr > policy.hurdle_apr and not stressed:
                disc = p.amount_minor * p.discount_bp // 10000
                item.update(
                    action="PAY_EARLY",
                    pay_date=iso_day(p.issue_day + p.discount_days),
                    pay_amount_minor=p.amount_minor - disc,
                    discount_captured_minor=disc,
                )
                captured += disc
        items.append(item)
    deferral: list[str] = []
    if forecast.weeks_with("SHORTFALL"):
        vend = {v.vendor_id: v for v in twin.vendors}
        cands = [p for p in twin.ap if p.is_open and vend[p.vendor_id].criticality < 3]
        cands.sort(
            key=lambda p: (
                vend[p.vendor_id].late_fee_minor,
                vend[p.vendor_id].criticality,
                p.due_day,
                p.reference,
            )
        )
        deferral = [p.invoice_id for p in cands]
    return {
        "hurdle_apr": decimal_str(policy.hurdle_apr, 6),
        "items": items,
        "discounts_captured_minor": captured,
        "deferral_order": deferral,
    }


def financing_need(forecast: Forecast) -> dict[str, Any] | None:
    short = forecast.weeks_with("SHORTFALL")
    if not short:
        return None
    return {
        "first_shortfall_week": short[0],
        "last_shortfall_week": short[-1],
        "shortfall_weeks": len(short),
        "max_deficit_minor": -forecast.min_closing,
        "amount_to_restore_buffer_minor": forecast.buffer_minor - forecast.min_closing,
    }
