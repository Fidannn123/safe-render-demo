"""Typed inputs of decide(). Everything the Guardian looks at is in GuardianInputs; nothing else.

The caller (payments, Phase 2) gathers these under the tenant-limits row lock and passes them in;
decide() itself never touches the database. The canonical JSON of GuardianInputs (RFC 8785) is what
inputs_sha256 binds, so it includes the registry account snapshot (D-029, PAYEE_CHANGED).

Integers are bounded to the I-JSON safe range (|n| <= 2^53 - 1) because RFC 8785 canonical JSON
cannot represent larger numbers; amounts beyond that are rejected at validation (API 422).
Lists are sorted by a stable key at validation so the hash does not depend on query order.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

SAFE_INT_MAX = 2**53 - 1


def _to_utc(value: dt.datetime) -> dt.datetime:
    return value.astimezone(dt.UTC)


SafeInt = Annotated[int, Field(ge=-SAFE_INT_MAX, le=SAFE_INT_MAX)]
NonNegInt = Annotated[int, Field(ge=0, le=SAFE_INT_MAX)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Iban = Annotated[str, Field(pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
PaymentState = Literal[
    "RECEIVED",
    "EXTRACTED",
    "PROPOSED",
    "DENIED",
    "HELD",
    "CLEARED",
    "APPROVED",
    "REJECTED",
    "RESERVED",
    "SUBMITTED",
    "SETTLED",
    "FAILED",
    "RECONCILED",
]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=False)


class ProposalInput(_Model):
    """The agent's propose_payment call plus the server-side ids and invoice_fingerprint."""

    payment_id: UUID
    invoice_id: UUID
    vendor_id: UUID
    account_id: UUID
    amount_minor: SafeInt
    currency: Annotated[str, Field(max_length=8)]
    reference: Annotated[str, Field(max_length=64)]
    due_date: dt.date
    invoice_fingerprint: Sha256Hex


class VendorSnapshot(_Model):
    vendor_id: UUID
    name: Annotated[str, Field(max_length=140)]
    blocked: bool = False


class RegistryAccount(_Model):
    """The vendor's verified registry bank account at decision time (never from invoice text)."""

    account_id: UUID
    vendor_id: UUID
    country: Country
    iban: Iban
    changed_at: UtcDatetime
    first_used_at: UtcDatetime | None = None

    def identity(self) -> tuple[str, str, str, str, str]:
        """Fields whose change means a different payee (PAYEE_CHANGED); first_used_at is excluded."""
        return (
            str(self.account_id),
            str(self.vendor_id),
            self.country,
            self.iban,
            self.changed_at.isoformat(),
        )


class KnownAccount(_Model):
    account_id: UUID
    iban: Iban


class KnownVendor(_Model):
    """Another vendor of the same tenant (look-alike comparisons)."""

    vendor_id: UUID
    name: Annotated[str, Field(max_length=140)]
    accounts: tuple[KnownAccount, ...] = ()

    @model_validator(mode="after")
    def _sorted(self) -> Self:
        object.__setattr__(self, "accounts", tuple(sorted(self.accounts, key=lambda a: str(a.account_id))))
        return self


class BankDetails(_Model):
    country: Country
    iban: Iban


class UntrustedText(_Model):
    field: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
    value: Annotated[str, Field(max_length=4000)]
    provenance: Literal["untrusted"] = "untrusted"


class InvoiceExtraction(_Model):
    """The validated ExtractedInvoice fields the Guardian uses. Claims, never payment instructions."""

    vendor_name: Annotated[str, Field(max_length=140)]
    reference: Annotated[str, Field(max_length=64)]
    amount_minor: SafeInt
    currency: Annotated[str, Field(max_length=8)]
    bank_details_on_invoice: BankDetails | None = None
    untrusted_text: tuple[UntrustedText, ...] = ()


class HistoryPayment(_Model):
    """A prior payment of this tenant (any vendor), used by duplicate/split/anomaly detectors."""

    payment_id: UUID
    vendor_id: UUID
    account_id: UUID
    amount_minor: SafeInt
    currency: Annotated[str, Field(max_length=8)]
    reference: Annotated[str, Field(max_length=64)]
    invoice_fingerprint: Sha256Hex
    state: PaymentState
    created_at: UtcDatetime


class RollingTotals(_Model):
    """Committed outflow in the rolling window (states policy.caps.counted_states), excluding this one."""

    outflow_24h_minor: NonNegInt
    new_payee_outflow_24h_minor: NonNegInt


class ForecastBuffer(_Model):
    """M7 forecast result with this payment applied: lowest projected balance vs the policy buffer."""

    buffer_minor: NonNegInt
    projected_min_balance_minor: SafeInt

    @property
    def breach(self) -> bool:
        return self.projected_min_balance_minor < self.buffer_minor


class NetworkResult(_Model):
    status: Literal["SKIP", "UNAVAILABLE", "HIT", "MISS"]
    reporters: NonNegInt = 0
    epoch: NonNegInt | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "HIT" and (self.reporters < 1 or self.epoch is None):
            raise ValueError("HIT needs reporters >= 1 and an epoch")
        if self.status == "MISS" and (self.reporters != 0 or self.epoch is None):
            raise ValueError("MISS needs reporters == 0 and an epoch")
        if self.status in ("SKIP", "UNAVAILABLE") and (self.reporters != 0 or self.epoch is not None):
            raise ValueError(f"{self.status} carries no reporters and no epoch")
        return self


class GuardianInputs(_Model):
    """All decide() inputs. inputs_sha256 = SHA-256(RFC 8785 JSON of this model)."""

    tenant_id: UUID
    proposal: ProposalInput
    vendor: VendorSnapshot
    registry_account: RegistryAccount | None
    known_vendors: tuple[KnownVendor, ...] = ()
    extraction: InvoiceExtraction
    history: tuple[HistoryPayment, ...] = ()
    rolling: RollingTotals
    forecast: ForecastBuffer
    network: NetworkResult
    tenant_frozen: bool
    now: UtcDatetime

    @model_validator(mode="after")
    def _sorted(self) -> Self:
        object.__setattr__(
            self, "known_vendors", tuple(sorted(self.known_vendors, key=lambda v: str(v.vendor_id)))
        )
        object.__setattr__(
            self, "history", tuple(sorted(self.history, key=lambda h: (h.created_at, str(h.payment_id))))
        )
        return self
