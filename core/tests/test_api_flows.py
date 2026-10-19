"""HTTP-level flows through the real middleware and views (SYNTHETIC data, no bank or intel calls).

Regression tests for defects found when the API was first exercised end to end: request bodies were
read as query parameters (every POST answered 422), and the self-approval check compared a user id with
the agent token id, so the owner who issued an agent token could approve that agent's proposals.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.twofactor.totp import TOTP
from django.test import Client
from django.utils import timezone
from django_otp.plugins.otp_totp.models import TOTPDevice

from apps.accounts.models import User
from apps.bank_adapter.service import process_outbox
from apps.ledger.service import post_template
from apps.ledger.tenancy import tenant_transaction
from apps.payments.service import lock_tenant_limits
from apps.tenants.models import Tenant
from apps.vendors.models import BankAccount, Vendor

pytestmark = pytest.mark.django_db(transaction=True)
REGISTRY_IBAN = "GB82WEST12345698765432"
ATTACKER_IBAN = "GB33BUKB20201555555555"
PASSWORD = "synthetic-test-password-1"  # noqa: S105 (test-only user)


def code(user: User) -> str:
    device = TOTPDevice.objects.get(user=user)
    return TOTP(bytes.fromhex(device.key), 6, hashes.SHA1(), 30).generate(time.time()).decode()  # noqa: S303


def call(client: Client, method: str, path: str, body: Any = None, **headers: str) -> tuple[int, Any]:
    if method == "GET":
        response = client.get("/api/v1" + path, headers=headers)
    else:
        headers.setdefault("Idempotency-Key", str(uuid.uuid4()))
        response = client.generic(
            method, "/api/v1" + path, json.dumps(body), content_type="application/json", headers=headers
        )
    return response.status_code, (json.loads(response.content) if response.content else None)


@pytest.fixture
def workspace() -> dict[str, Any]:
    tenant = uuid.uuid4()
    Tenant.objects.create(id=tenant, name="Synthetic API SME")
    users = {}
    for role in ("owner", "approver"):
        user = User.objects.create_user(f"{role}-{tenant}", password=PASSWORD, tenant_id=tenant, role=role)
        TOTPDevice.objects.create(user=user, name=role, key="3132333435363738393031323334353637383930")
        users[role] = user
    with tenant_transaction(tenant):
        lock_tenant_limits(tenant)
        post_template(tenant, "opening_balance", 5_000_000, "USD")
        vendor = Vendor.objects.create(tenant_id=tenant, name="Synthetic Supplier Ltd")
        long_ago = timezone.now() - timedelta(days=120)
        account = BankAccount(
            tenant_id=tenant,
            vendor=vendor,
            country="GB",
            holder_name=vendor.name,
            verified_at=long_ago,
            changed_at=long_ago,
            first_used_at=long_ago,
        )
        account.iban = REGISTRY_IBAN
        account.save()
    return {"tenant": tenant, "users": users, "vendor": vendor}


def login(user: User) -> Client:
    client = Client()
    status, body = call(
        client, "POST", "/auth/login", {"username": user.username, "password": PASSWORD, "otp": code(user)}
    )
    assert status == 200, body
    return client


def invoice_text(reference: str, iban: str) -> str:
    return (
        "From: Synthetic Supplier Ltd\n"
        f"Invoice number: {reference}\n"
        "Amount due: USD 1,250.00\n"
        "Due date: 2026-12-01\n"
        f"IBAN: {iban}\n"
    )


def agent_pays(owner: Client, reference: str, iban: str) -> tuple[str, dict[str, Any]]:
    """The agent ingests an invoice, waits for extraction and proposes payment to the registry account."""
    status, token = call(owner, "POST", "/auth/agent-tokens", {"name": "agent", "scopes": ["T0", "T1", "T2"]})
    assert status == 201, token
    bearer = {"Authorization": f"Bearer {token['token']}"}
    agent = Client()
    status, created = call(
        agent,
        "POST",
        "/invoices",
        {
            "source": "email",
            "content_type": "text/plain",
            "received_at": "2026-10-19T08:00:00Z",
            "body_text": invoice_text(reference, iban),
        },
        **bearer,
    )
    assert status == 201, created
    process_outbox()
    _, invoice = call(agent, "GET", f"/invoices/{created['invoice_id']}", **bearer)
    _, vendors = call(agent, "GET", "/vendors?q=Synthetic", **bearer)
    vendor = vendors[0]
    ex = invoice["extracted"]
    status, decision = call(
        agent,
        "POST",
        "/agent/proposals",
        {
            "invoice_id": created["invoice_id"],
            "vendor_id": vendor["vendor_id"],
            "account_id": vendor["verified_account"]["account_id"],
            "amount_minor": ex["amount_minor"],
            "currency": ex["currency"],
            "reference": ex["reference"],
            "due_date": ex["due_date"],
        },
        **bearer,
    )
    assert status == 201, decision
    return created["payment_id"], {"decision": decision, "bearer": bearer, "agent": agent}


def test_login_reads_json_body_and_rejects_bad_codes(workspace: dict[str, Any]) -> None:
    owner = workspace["users"]["owner"]
    status, body = call(
        Client(), "POST", "/auth/login", {"username": owner.username, "password": PASSWORD, "otp": "000000"}
    )
    assert (status, body["code"]) == (401, "UNAUTHENTICATED")
    status, body = call(Client(), "POST", "/auth/login", {"username": owner.username, "password": PASSWORD})
    assert (status, body["code"]) == (422, "VALIDATION_FAILED")
    status, session = call(login(owner), "GET", "/auth/session")
    assert status == 200
    assert session["roles"] == ["owner"]


def test_routine_invoice_is_allowed_and_reserved(workspace: dict[str, Any]) -> None:
    payment_id, ctx = agent_pays(login(workspace["users"]["owner"]), "INV-1001", REGISTRY_IBAN)
    assert ctx["decision"]["verdict"] == "ALLOW"
    _, payment = call(ctx["agent"], "GET", f"/payments/{payment_id}", **ctx["bearer"])
    assert payment["state"] == "RESERVED"


def test_bank_detail_swap_is_held_and_four_eyes_hold(workspace: dict[str, Any]) -> None:
    owner = login(workspace["users"]["owner"])
    payment_id, ctx = agent_pays(owner, "INV-1002", ATTACKER_IBAN)
    decision = ctx["decision"]
    assert decision["verdict"] == "HOLD"
    assert "BANK_DETAIL_MISMATCH" in {r["code"] for r in decision["reasons"]}

    approve = {"reason": "checked with the vendor", "acknowledged_warnings": []}
    # The agent's own token cannot reach human-only endpoints.
    status, body = call(ctx["agent"], "POST", f"/payments/{payment_id}/approve", approve, **ctx["bearer"])
    assert (status, body["code"]) == (403, "FORBIDDEN_SCOPE")
    # The owner who issued the agent's token is the initiator: self-approval is refused.
    status, body = call(owner, "POST", f"/payments/{payment_id}/approve", approve)
    assert (status, body["code"]) == (409, "SELF_APPROVAL")
    # An independent approver can approve; the payment is reserved in the same transaction.
    status, body = call(
        login(workspace["users"]["approver"]), "POST", f"/payments/{payment_id}/approve", approve
    )
    assert status == 200, body
    assert body["state"] == "RESERVED"


def test_freeze_revokes_agent_tokens_and_unfreeze_needs_owner(workspace: dict[str, Any]) -> None:
    owner = login(workspace["users"]["owner"])
    tenant = workspace["tenant"]
    _, token = call(owner, "POST", "/auth/agent-tokens", {"name": "agent", "scopes": ["T1"]})
    status, body = call(
        login(workspace["users"]["approver"]), "POST", f"/tenants/{tenant}/freeze", {"reason": "drill"}
    )
    assert status == 200
    assert body["frozen"] is True
    status, _ = call(Client(), "GET", "/payments", Authorization=f"Bearer {token['token']}")
    assert status == 401
    status, body = call(owner, "POST", f"/tenants/{tenant}/unfreeze", {"reason": "drill over"})
    assert status == 200
    assert body["frozen"] is False
