"""Recovery tests exercise real state transitions, ledger triggers and Ed25519 receipt verification."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.utils import timezone

from apps.bank_adapter.client import SignedBankClient
from apps.bank_adapter.models import Receipt, TransferSubmission
from apps.bank_adapter.service import process_outbox
from apps.bank_adapter.signing import RemoteError, canonical, sign_object
from apps.ledger.tenancy import tenant_transaction
from apps.payments.models import OutboxMessage, TenantLimits
from apps.payments.tests.helpers import (
    REGISTRY_ACCOUNT,
    VENDOR,
    assert_ledger_invariants,
    bal,
    decide,
    fund,
    proposed,
    veto,
)
from apps.recon.models import Incident
from apps.recon.service import reconcile_statement
from apps.tenants.models import Tenant
from apps.vendors.models import BankAccount, Vendor

pytestmark = pytest.mark.django_db(transaction=True)
IBAN = "GB82WEST12345698765432"


def refresh(tenant: uuid.UUID, row: Any) -> None:
    """Payments are FORCE RLS: re-read only inside the tenant's transaction."""
    with tenant_transaction(tenant):
        row.refresh_from_db()


class BankTransport:
    def __init__(self, key: Ed25519PrivateKey, debtor: uuid.UUID) -> None:
        self.key, self.debtor = key, debtor
        self.calls: list[dict[str, Any]] = []
        self.transfers: dict[str, dict[str, Any]] = {}
        self.timeout_once = False
        self.status = "SETTLED"
        self.tamper = False

    def request(self, method: str, target: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        assert method == "POST"
        assert target == "/v1/transfers"
        assert payload is not None
        self.calls.append(payload)
        e2e = payload["end_to_end_id"]
        if e2e not in self.transfers:
            transfer_id = str(uuid.uuid4())
            receipt = sign_object(
                {
                    "receipt_id": str(uuid.uuid4()),
                    "transfer_id": transfer_id,
                    "end_to_end_id": e2e,
                    "status": self.status,
                    "debtor": {"account_id": str(self.debtor), "iban": IBAN},
                    "creditor": payload["creditor"],
                    "amount_minor": payload["amount_minor"],
                    "currency": payload["currency"],
                    "remittance_info": payload["remittance_info"],
                    "booked_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "failure_reason": None if self.status == "SETTLED" else "INJECTED_FAILURE",
                    "bank_id": "SAFEPAY-SANDBOX",
                    "simulated": True,
                    "key_id": "bank:attestation:1",
                },
                self.key,
            )
            self.transfers[e2e] = {
                "transfer_id": transfer_id,
                "end_to_end_id": e2e,
                "status": self.status,
                "request_sha256": hashlib.sha256(canonical(payload)).hexdigest(),
                "receipt": receipt,
            }
        if self.timeout_once:
            self.timeout_once = False
            raise RemoteError("REMOTE_UNAVAILABLE")
        result: dict[str, Any] = json.loads(json.dumps(self.transfers[e2e]))
        if self.tamper:
            result["receipt"]["amount_minor"] += 1
        return result


@pytest.fixture
def setup_bank(settings: Any, tmp_path: Path) -> tuple[uuid.UUID, SignedBankClient, BankTransport]:
    settings.SAFEPAY_KEYSTORE_DIR = tmp_path / "keystore"
    tenant = uuid.uuid4()
    Tenant.objects.create(id=tenant, name="Synthetic test SME", bank_account_id=tenant)
    with tenant_transaction(tenant):
        vendor = Vendor.objects.create(id=VENDOR, tenant_id=tenant, name="Synthetic Supplier")
        account = BankAccount(
            id=REGISTRY_ACCOUNT, tenant_id=tenant, vendor=vendor, country="GB", holder_name=vendor.name
        )
        account.iban = IBAN
        account.save()
    fund(tenant, 2_000_000)
    key = Ed25519PrivateKey.generate()
    transport = BankTransport(key, tenant)
    client = object.__new__(SignedBankClient)
    client.transport = transport  # type: ignore[assignment]
    client.public_key, client.key_id = key.public_key(), "bank:attestation:1"
    return tenant, client, transport


def test_timeout_after_bank_commit_retries_exact_request_once(setup_bank: Any) -> None:
    tenant, client, bank = setup_bank
    payment = decide(proposed(tenant, 100_000), "ALLOW")
    bank.timeout_once = True
    now = timezone.now()
    assert process_outbox(client=client, now=now)["retried"] == 1
    refresh(tenant, payment)
    assert payment.state == "SUBMITTED"
    assert bal(tenant, "Bank:in_transit") == 100_000
    with tenant_transaction(tenant):
        submission = TransferSubmission.objects.get(payment_id=payment.id)
        assert IBAN not in submission.request_ciphertext
        # Registry edits after uncertain settlement must not alter the idempotent replay.
        account = BankAccount.objects.get(id=REGISTRY_ACCOUNT)
        account.iban = "GB33BUKB20201555555555"
        account.save()
    assert process_outbox(client=client, now=now + timedelta(minutes=10))["delivered"] == 1
    refresh(tenant, payment)
    assert payment.state == "SETTLED"
    assert bank.calls[0] == bank.calls[1]
    assert len(bank.transfers) == 1
    assert bal(tenant, "Bank:available") == 1_900_000
    assert bal(tenant, "Bank:in_transit") == 0
    with tenant_transaction(tenant):
        receipt = Receipt.objects.get(payment_id=payment.id)
        assert IBAN not in receipt.document_ciphertext
        assert receipt.document["creditor"]["iban"] == IBAN
    assert_ledger_invariants(tenant)


def test_failed_receipt_releases_funds(setup_bank: Any) -> None:
    tenant, client, bank = setup_bank
    bank.status = "FAILED"
    payment = decide(proposed(tenant, 100_000), "ALLOW")
    process_outbox(client=client)
    refresh(tenant, payment)
    assert payment.state == "FAILED"
    assert bal(tenant, "Bank:available") == 2_000_000
    assert_ledger_invariants(tenant)


def test_forged_receipt_cannot_settle(setup_bank: Any) -> None:
    tenant, client, bank = setup_bank
    bank.tamper = True
    payment = decide(proposed(tenant, 100_000), "ALLOW")
    process_outbox(client=client)
    refresh(tenant, payment)
    assert payment.state == "SUBMITTED"
    with tenant_transaction(tenant):
        assert Receipt.objects.count() == 0
    assert OutboxMessage.objects.get(payment_id=payment.id).last_error == "SIGNATURE_INVALID"
    assert_ledger_invariants(tenant)


def test_freeze_and_t2_veto_stop_bank_calls(setup_bank: Any) -> None:
    tenant, client, bank = setup_bank
    now = timezone.now()
    payment = decide(proposed(tenant, 80_000), "GUARDED", now=now)
    assert process_outbox(client=client, now=now)["processed"] == 0
    veto(payment, now=now + timedelta(seconds=1))
    process_outbox(client=client, now=now + timedelta(minutes=11))
    assert not bank.calls
    other = decide(proposed(tenant, 70_000), "ALLOW")
    with tenant_transaction(tenant):
        TenantLimits.objects.filter(tenant_id=tenant).update(frozen=True)
    process_outbox(client=client, now=now + timedelta(minutes=12))
    refresh(tenant, other)
    assert other.state == "RESERVED"
    assert not bank.calls
    assert_ledger_invariants(tenant)


def test_bank_mismatch_freezes_and_incident_is_idempotent(setup_bank: Any) -> None:
    tenant, _client, _bank = setup_bank
    statement = {
        "account_id": str(tenant),
        "date": timezone.now().date().isoformat(),
        "currency": "USD",
        "closing_balance_minor": 1_999_000,
        "lines": [
            {
                "end_to_end_id": str(uuid.uuid4()),
                "direction": "DEBIT",
                "amount_minor": 1_000,
                "counterparty": {"iban": IBAN},
            }
        ],
    }
    assert reconcile_statement(tenant, statement)["frozen"] is True
    with tenant_transaction(tenant):
        count = Incident.objects.count()
    reconcile_statement(tenant, statement)
    with tenant_transaction(tenant):
        assert Incident.objects.count() == count
        assert Incident.objects.filter(code="MISSING_IN_LEDGER").exists()
    assert_ledger_invariants(tenant)


def test_retry_exhaustion_emits_incident_without_refunding_unknown_settlement(setup_bank: Any) -> None:
    tenant, client, bank = setup_bank
    payment = decide(proposed(tenant, 10_000), "ALLOW")
    bank.tamper = True
    for attempt in range(6):
        process_outbox(client=client, now=timezone.now() + timedelta(hours=attempt))
    message = OutboxMessage.objects.get(payment_id=payment.id)
    assert message.status == "DEAD"
    assert message.attempts == 6
    assert bal(tenant, "Bank:in_transit") == 10_000
    assert_ledger_invariants(tenant)
