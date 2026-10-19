"""Signed SIMULATED-bank requests and strict receipt binding to the original transfer."""

from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

from django.conf import settings

from .contracts import validate_document
from .signing import RemoteError, SignedTransport, canonical, load_private, load_public, verify_object


class SignedBankClient:
    def __init__(self, transport: SignedTransport | None = None) -> None:
        directory = Path(settings.SAFEPAY_KEYS_DIR) / "ed25519"
        self.transport = transport or SignedTransport(
            settings.SAFEPAY_BANK_URL,
            load_private(
                Path(os.getenv("SAFEPAY_CORE_SERVICE_KEY_FILE", str(directory / "core_service.pem")))
            ),
            os.getenv("SAFEPAY_CORE_SERVICE_KEY_ID", "core:service:1"),
        )
        self.public_key = load_public(
            Path(os.getenv("SAFEPAY_BANK_ATTESTATION_PUB_FILE", str(directory / "bank_attestation.pub")))
        )
        self.key_id = os.getenv("SAFEPAY_BANK_ATTESTATION_KEY_ID", "bank:attestation:1")

    def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        transfer = self.transport.request("POST", "/v1/transfers", request)
        self.verify_transfer(transfer, request)
        return transfer

    def get_transfer(self, payment_id: UUID) -> dict[str, Any]:
        return self.transport.request("GET", f"/v1/transfers/{payment_id}")

    def verify_transfer(self, transfer: dict[str, Any], request: dict[str, Any]) -> None:
        receipt = transfer.get("receipt")
        if not isinstance(receipt, dict):
            raise RemoteError("RECEIPT_INVALID")
        validate_document("Receipt", receipt)
        verify_object(receipt, self.public_key, self.key_id)
        if (
            transfer.get("end_to_end_id") != request["end_to_end_id"]
            or transfer.get("request_sha256") != hashlib.sha256(canonical(request)).hexdigest()
            or receipt["end_to_end_id"] != request["end_to_end_id"]
            or receipt["transfer_id"] != transfer.get("transfer_id")
            or receipt["status"] != transfer.get("status")
            or receipt["debtor"]["account_id"] != request["debtor_account_id"]
            or receipt["creditor"] != request["creditor"]
            or receipt["amount_minor"] != request["amount_minor"]
            or receipt["currency"] != request["currency"]
            or receipt.get("remittance_info", "") != request.get("remittance_info", "")
        ):
            raise RemoteError("RECEIPT_INVALID")

    def statement(self, account_id: UUID, day: date) -> dict[str, Any]:
        target = "/v1/statements?" + urlencode({"account_id": str(account_id), "date": day.isoformat()})
        result = self.transport.request("GET", target)
        validate_document("Statement", result)
        verify_object(result, self.public_key, self.key_id)
        if result["account_id"] != str(account_id) or result["date"] != day.isoformat():
            raise RemoteError("STATEMENT_INVALID")
        total = result["opening_balance_minor"] + sum(
            line["amount_minor"] * (1 if line["direction"] == "CREDIT" else -1) for line in result["lines"]
        )
        if total != result["closing_balance_minor"]:
            raise RemoteError("STATEMENT_INVALID")
        return result
