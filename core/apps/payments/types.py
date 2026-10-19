"""Typed inputs of transition()."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

ACTOR_TYPES = ("human", "agent", "system", "guardian", "bank", "reconciler", "intel")  # common.json Actor


@dataclass(frozen=True, slots=True)
class Actor:
    type: str
    id: str

    def __post_init__(self) -> None:
        if self.type not in ACTOR_TYPES:
            raise ValueError(f"unknown actor type {self.type!r}")
        if not self.id or len(self.id) > 128:
            raise ValueError("actor id must be 1..128 characters")

    def as_dict(self) -> dict[str, str]:
        return {"type": self.type, "id": self.id}


SYSTEM = Actor("system", "system")


@dataclass(frozen=True, slots=True)
class ExtractionBinding:
    """Validated extractor output bound at RECEIVED -> EXTRACTED (vendor_id None = not in the registry)."""

    vendor_id: uuid.UUID | None
    amount_minor: int
    currency: str
    reference: str | None = None
    due_date: date | None = None
    expense_account: str = "Expense"


@dataclass(frozen=True, slots=True)
class ProposalBinding:
    """propose_payment arguments. account_id is the vendor's REGISTRY account (never invoice text)."""

    vendor_id: uuid.UUID
    account_id: uuid.UUID
    amount_minor: int
    currency: str


@dataclass(frozen=True, slots=True)
class DecisionBinding:
    """Guardian Decision fields copied to the payment on decision_* transitions."""

    decision_id: uuid.UUID
    tier: str | None  # None for DENY
    cooloff_until: datetime | None = None
    approvals_required: int = 0
    approval_warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TransitionContext:
    now: datetime | None = None
    reason: str | None = None
    reject_reason: str | None = None
    approver_ids: tuple[str, ...] = ()
    acknowledged_warnings: tuple[str, ...] = ()
    step_up_at: datetime | None = None
    actor_role: str | None = None
    # Guards satisfied by a check the caller already ran (Guardian recheck, receipt verification, ...).
    # Default guard implementations for those names fail closed unless the name is listed here.
    attested: frozenset[str] = frozenset()
    extraction: ExtractionBinding | None = None
    proposal: ProposalBinding | None = None
    decision: DecisionBinding | None = None
    audit_data: Mapping[str, Any] = field(default_factory=dict)
