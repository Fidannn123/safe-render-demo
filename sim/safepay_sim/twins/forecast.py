"""Independent oracle for the 13-week cash-flow forecast (architecture.md section 2.6).

Method (written down so core/apps/cashflow can be compared with it exactly):
  * as_of = day 90 (2026-09-29). Week k covers days [as_of + 7k, as_of + 7k + 6], k = 0..12.
  * opening = bank balance at the end of the 90-day history.
  * inflows: each open receivable x customer's on-time rate (paid invoices with paid <= due, over all
    paid invoices of the customer; 1 if none), rounded half-even to minor units, expected on
    max(as_of, due + median_low(max(0, paid - due))). Beyond week 12 it is ignored.
  * outflows: open payables on their due dates (discounts ignored) + recurring opex projected from
    the last occurrence with the detected period (median_low of intervals, same payee), same amount.
  * buffer = max(buffer_floor, 2 x median weekly opex), median over the 12 complete weeks before as_of
    of direct-debit opex only (vendor payments excluded).
  * status per week from the week's closing balance: SHORTFALL if < 0, else LOW_BUFFER if < buffer.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from fractions import Fraction

from .model import AS_OF_DAY, FORECAST_WEEKS, Twin, iso_day
from .policy import Policy


@dataclass(frozen=True)
class Week:
    index: int
    start_day: int
    inflow_minor: int
    outflow_minor: int
    closing_minor: int
    status: str

    def to_json(self) -> dict[str, object]:
        return {
            "week": self.index,
            "start_date": iso_day(self.start_day),
            "end_date": iso_day(self.start_day + 6),
            "inflow_minor": self.inflow_minor,
            "outflow_minor": self.outflow_minor,
            "closing_minor": self.closing_minor,
            "status": self.status,
        }


@dataclass(frozen=True)
class Forecast:
    opening_minor: int
    buffer_minor: int
    median_weekly_opex_minor: int
    weeks: tuple[Week, ...]

    @property
    def min_closing(self) -> int:
        return min(w.closing_minor for w in self.weeks)

    def weeks_with(self, status: str) -> list[int]:
        return [w.index for w in self.weeks if w.status == status]


def customer_behaviour(twin: Twin, customer_id: str) -> tuple[Fraction, int]:
    paid = [a for a in twin.ar if a.customer_id == customer_id and a.paid_day is not None]
    if not paid:
        return Fraction(1), 0
    on_time = sum(1 for a in paid if a.paid_day is not None and a.paid_day <= a.due_day)
    delays = [max(0, (a.paid_day or 0) - a.due_day) for a in paid]
    return Fraction(on_time, len(paid)), int(statistics.median_low(delays))


def median_weekly_opex(twin: Twin) -> int:
    totals = []
    for j in range(12):
        lo, hi = AS_OF_DAY - 7 * (j + 1), AS_OF_DAY - 7 * j
        totals.append(sum(d.amount_minor for d in twin.opex_debits if lo <= d.day < hi))
    totals.sort()
    med = Fraction(totals[5] + totals[6], 2)
    return round(med)


def opex_projection(twin: Twin, horizon_end: int) -> list[tuple[int, int]]:
    """(day, amount) of projected recurring opex in [as_of, horizon_end)."""
    by_payee: dict[str, list[tuple[int, int]]] = {}
    for d in twin.opex_debits:
        by_payee.setdefault(d.payee, []).append((d.day, d.amount_minor))
    out: list[tuple[int, int]] = []
    for occ in by_payee.values():
        occ.sort()
        if len(occ) < 2:
            continue
        intervals = [b[0] - a[0] for a, b in itertools.pairwise(occ)]
        period = int(statistics.median_low(intervals))
        day, amount = occ[-1]
        day += period
        while day < horizon_end:
            if day >= AS_OF_DAY:
                out.append((day, amount))
            day += period
    return out


def compute_forecast(twin: Twin, policy: Policy) -> Forecast:
    horizon_end = AS_OF_DAY + 7 * FORECAST_WEEKS
    inflow = [0] * FORECAST_WEEKS
    outflow = [0] * FORECAST_WEEKS

    def bucket(day: int) -> int | None:
        k = (max(day, AS_OF_DAY) - AS_OF_DAY) // 7
        return k if k < FORECAST_WEEKS else None

    for c in twin.customers:
        rate, delay = customer_behaviour(twin, c.customer_id)
        for a in twin.ar:
            if a.customer_id != c.customer_id or not a.is_open:
                continue
            k = bucket(a.due_day + delay)
            if k is not None:
                inflow[k] += round(a.amount_minor * rate)
    for p in twin.ap:
        if p.is_open:
            k = bucket(p.due_day)
            if k is not None:
                outflow[k] += p.amount_minor
    for day, amount in opex_projection(twin, horizon_end):
        k = bucket(day)
        if k is not None:
            outflow[k] += amount

    med = median_weekly_opex(twin)
    buffer = max(policy.buffer_floor, policy.median_opex_weeks * med)
    opening = twin.closing_balance()
    running = opening
    weeks: list[Week] = []
    for k in range(FORECAST_WEEKS):
        running += inflow[k] - outflow[k]
        status = "SHORTFALL" if running < 0 else "LOW_BUFFER" if running < buffer else "OK"
        weeks.append(Week(k, AS_OF_DAY + 7 * k, inflow[k], outflow[k], running, status))
    return Forecast(opening, buffer, med, tuple(weeks))
