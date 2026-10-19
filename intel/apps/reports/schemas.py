"""Request/response schemas mirroring spec/schemas/Report.json, Receipt.json and ReportRecord."""

from __future__ import annotations

from typing import Annotated, Literal

from ninja import Schema
from pydantic import ConfigDict, Field

Uuid = Annotated[str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")]
Country = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
Iban = Annotated[str, Field(pattern=r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")]
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
NonceHex = Annotated[str, Field(pattern=r"^[0-9a-f]{32,64}$")]
KeyId = Annotated[str, Field(pattern=r"^[a-z0-9][a-z0-9._:-]{2,127}$")]
Signature = Annotated[str, Field(pattern=r"^[A-Za-z0-9+/]{86}==$")]
Timestamp = Annotated[str, Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$")]


class Strict(Schema):
    model_config = ConfigDict(extra="forbid")


class PayeeIn(Strict):
    country: Country
    iban: Iban
    name: str | None = Field(default=None, max_length=140)


class DebtorIn(Strict):
    account_id: Uuid
    iban: Iban


class ReceiptIn(Strict):
    receipt_id: Uuid
    transfer_id: Uuid
    end_to_end_id: Uuid
    status: Literal["SETTLED", "FAILED"]
    debtor: DebtorIn
    creditor: PayeeIn
    amount_minor: int = Field(ge=1, le=9223372036854775807)
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    remittance_info: str | None = Field(default=None, max_length=140)
    booked_at: Timestamp
    failure_reason: Literal["INSUFFICIENT_FUNDS", "CREDITOR_ACCOUNT_INVALID", "INJECTED_FAILURE"] | None
    bank_id: Literal["SAFEPAY-SANDBOX"]
    simulated: Literal[True]
    key_id: KeyId
    sig: Signature


class ReportIn(Strict):
    report_id: Uuid
    member_id: Uuid
    payee: PayeeIn
    category: Literal["INVOICE_REDIRECTION", "FAKE_VENDOR", "IMPERSONATION", "OTHER_FRAUD"]
    receipt: ReceiptIn
    evidence_sha256: Sha256Hex
    created_at: Timestamp
    nonce: NonceHex
    key_id: KeyId
    sig: Signature


class ReportRecordOut(Schema):
    report_id: str
    status: Literal["ADMITTED", "RETRACTED", "EXPIRED"]
    payee_counted: bool
    expires_at: str
