"""Request schemas mirroring spec/openapi-bank.yaml (strict: no coercion, no extra fields)."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
DATE_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
INT64_MAX = 9223372036854775807

Uuid = Annotated[str, Field(pattern=UUID_PATTERN)]
Iban = Annotated[str, Field(pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Currency = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
AmountMinor = Annotated[int, Field(ge=1, le=INT64_MAX)]


class StrictSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="before")
    @classmethod
    def _no_nulls(cls, data: Any) -> Any:
        """The spec has no nullable request fields: optional means absent, never null."""
        if isinstance(data, dict):
            nulls = [k for k, v in data.items() if v is None]
            if nulls:
                raise ValueError(f"null not allowed for {', '.join(sorted(map(str, nulls)))}")
        return data


class PayeeIn(StrictSchema):
    country: Country
    iban: Iban
    name: Annotated[str, Field(max_length=140)] | None = None


class TransferRequestIn(StrictSchema):
    end_to_end_id: Uuid
    debtor_account_id: Uuid
    creditor: PayeeIn
    amount_minor: AmountMinor
    currency: Currency
    remittance_info: Annotated[str, Field(max_length=140)] | None = None


FailReason = Literal["INSUFFICIENT_FUNDS", "CREDITOR_ACCOUNT_INVALID", "INJECTED_FAILURE"]


class FailNextRequestIn(StrictSchema):
    count: Annotated[int, Field(ge=0, le=100)]
    reason: FailReason
    mode: Literal["fail", "timeout"] = "fail"
