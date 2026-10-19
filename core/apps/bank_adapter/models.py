"""Verified receipts and immutable request bindings for recoverable, idempotent bank delivery."""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.db import models


class TransferSubmission(models.Model):
    payment_id = models.UUIDField(primary_key=True)
    tenant_id = models.UUIDField()
    request_ciphertext = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_adapter_submission"

    def __str__(self) -> str:
        return f"submission {self.payment_id}"

    @property
    def request(self) -> dict[str, Any]:
        from apps.vendors.models import decrypt_iban  # noqa: PLC0415

        result: dict[str, Any] = json.loads(decrypt_iban(self.request_ciphertext, self.tenant_id))
        return result

    def set_request(self, request: dict[str, Any]) -> None:
        from apps.vendors.models import encrypt_iban  # noqa: PLC0415

        self.request_ciphertext = encrypt_iban(json.dumps(request), self.tenant_id)


class Receipt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    payment_id = models.UUIDField(unique=True)
    document_ciphertext = models.TextField()
    verified_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "bank_adapter_receipt"

    def __str__(self) -> str:
        return f"receipt {self.id}"

    @property
    def document(self) -> dict[str, Any]:
        from apps.vendors.models import decrypt_iban  # noqa: PLC0415

        result: dict[str, Any] = json.loads(decrypt_iban(self.document_ciphertext, self.tenant_id))
        return result

    def set_document(self, document: dict[str, Any]) -> None:
        from apps.vendors.models import encrypt_iban  # noqa: PLC0415

        self.document_ciphertext = encrypt_iban(json.dumps(document), self.tenant_id)
