"""Simulated clock for SYNTHETIC seeding and the evidence harness only. Never used by request handling.

Core reads time through django.utils.timezone.now() (including auto_now_add fields), so pinning that one
function moves every timestamp the tenant domain writes: seeded history gets its historical dates, and
harness scenarios run at their simulated times. External services (bank, intel) keep wall-clock time.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator
from contextlib import contextmanager
from unittest import mock

from django.utils import timezone

_REAL_NOW = timezone.now


def wall_clock() -> dt.datetime:
    """Real UTC time, unaffected by sim_clock (for services that validate against wall time)."""
    return _REAL_NOW()


class SimClock:
    """A settable clock; while installed, django.utils.timezone.now() returns its value."""

    def __init__(self, start: dt.datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("sim clock needs an aware datetime")
        self.value = start

    def now(self) -> dt.datetime:
        return self.value

    def set(self, value: dt.datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("sim clock needs an aware datetime")
        self.value = value

    def advance(self, delta: dt.timedelta) -> None:
        self.value += delta


@contextmanager
def sim_clock(clock: SimClock) -> Iterator[SimClock]:
    with mock.patch.object(timezone, "now", clock.now):
        yield clock
