"""Request-signature middleware (D-012): forged, expired, replayed and unsigned requests are rejected."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import Client

from apps.accounts.models import Account
from tests.conftest import CORE_KEY_ID, SignedClient, transfer_request

pytestmark = pytest.mark.django_db


def problem(response: Any, code: str, status: int, validate: Any) -> None:
    assert response.status_code == status, response.content
    assert response["Content-Type"] == "application/problem+json"
    body = json.loads(response.content)
    assert body["code"] == code
    assert body["type"] == f"urn:safepay:problem:{code}"
    validate("Problem.json", body)


def test_signed_request_accepted(bank: SignedClient, account: Account) -> None:
    assert bank.get(f"/v1/accounts/{account.account_id}").status_code == 200


def test_unsigned_request_rejected(client: Client, account: Account, validate: Any) -> None:
    problem(client.get(f"/v1/accounts/{account.account_id}"), "SIGNATURE_INVALID", 401, validate)


def test_admin_requires_signature(client: Client, validate: Any) -> None:
    response = client.post(
        "/admin/fail-next", data={"count": 1, "reason": "INJECTED_FAILURE"}, content_type="application/json"
    )
    problem(response, "SIGNATURE_INVALID", 401, validate)


def test_forged_signature_rejected(client: Client, account: Account, validate: Any) -> None:
    forger = SignedClient(client, Ed25519PrivateKey.generate())  # right key id, wrong key
    problem(forger.get(f"/v1/accounts/{account.account_id}"), "SIGNATURE_INVALID", 401, validate)


def test_unknown_key_id_rejected(client: Client, keys: Any, account: Account, validate: Any) -> None:
    other = SignedClient(client, keys.core, key_id="intel:service:1")
    problem(other.get(f"/v1/accounts/{account.account_id}"), "SIGNATURE_INVALID", 401, validate)


def test_tampered_body_rejected(bank: SignedClient, account: Account, validate: Any) -> None:
    body = json.dumps(transfer_request(account)).encode()
    headers = bank.headers("POST", "/v1/transfers", "", body)
    tampered = body.replace(b"125000", b"999999")
    response = bank.client.post(
        "/v1/transfers", data=tampered, content_type="application/json", headers=headers
    )
    problem(response, "SIGNATURE_INVALID", 401, validate)
    assert Account.objects.get(pk=account.pk).balance_minor == account.balance_minor


def test_tampered_query_rejected(bank: SignedClient, account: Account, validate: Any) -> None:
    query = f"account_id={account.account_id}&date=2026-10-19"
    headers = bank.headers("GET", "/v1/statements", query)
    response = bank.client.get(
        f"/v1/statements?account_id={account.account_id}&date=2026-10-18", headers=headers
    )
    problem(response, "SIGNATURE_INVALID", 401, validate)


@pytest.mark.parametrize("offset", [-301, 301, -10_000])
def test_expired_or_future_timestamp_rejected(
    bank: SignedClient, account: Account, offset: int, validate: Any
) -> None:
    response = bank.get(f"/v1/accounts/{account.account_id}", ts=int(time.time()) + offset)
    problem(response, "REQUEST_EXPIRED", 401, validate)


def test_timestamp_within_skew_accepted(bank: SignedClient, account: Account) -> None:
    assert bank.get(f"/v1/accounts/{account.account_id}", ts=int(time.time()) - 250).status_code == 200


def test_replayed_nonce_rejected(bank: SignedClient, account: Account, validate: Any) -> None:
    path = f"/v1/accounts/{account.account_id}"
    headers = bank.headers("GET", path)
    assert bank.client.get(path, headers=headers).status_code == 200
    problem(bank.client.get(path, headers=headers), "NONCE_REPLAYED", 401, validate)


def test_forged_request_does_not_burn_nonce(client: Client, bank: SignedClient, account: Account) -> None:
    path = f"/v1/accounts/{account.account_id}"
    nonce = "ab" * 16
    forger = SignedClient(client, Ed25519PrivateKey.generate())
    assert forger.get(path, nonce=nonce).status_code == 401
    assert bank.get(path, nonce=nonce).status_code == 200


@pytest.mark.parametrize(
    ("header", "value"),
    [
        ("X-SafePay-Timestamp", "17600000"),
        ("X-SafePay-Nonce", "ABCDEF0123456789ABCDEF0123456789"),
        ("X-SafePay-Nonce", "abc"),
        ("X-SafePay-Key-Id", "Core Service"),
        ("X-SafePay-Signature", ""),
    ],
)
def test_malformed_headers_rejected(
    bank: SignedClient, account: Account, header: str, value: str, validate: Any
) -> None:
    path = f"/v1/accounts/{account.account_id}"
    headers = {**bank.headers("GET", path), header: value}
    problem(bank.client.get(path, headers=headers), "SIGNATURE_INVALID", 401, validate)


def test_health_and_openapi_stay_unsigned(client: Client) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/v1/openapi.json").status_code == 200


def test_key_id_constant_matches_header_pattern() -> None:
    from safepay_bank.reqsig import KEY_ID_RE  # noqa: PLC0415

    assert KEY_ID_RE.match(CORE_KEY_ID)
