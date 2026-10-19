"""Shared fixtures for M4 intel tests: throwaway keys, admitted members, signed requests, spec validation."""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.conf import settings
from django.test import Client
from django.test.client import Client as DjangoClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from apps.members.models import Member, MemberAccount, MemberKey
from safepay_intel.crypto import request_signing_string, sign_object

BASE = "https://safepay.local/"


@cache
def _registry() -> Registry[Any]:
    spec_dir: Path = settings.SAFEPAY_SPEC_DIR
    resources: list[tuple[str, Resource[Any]]] = []
    for path in sorted((spec_dir / "schemas").glob("*.json")):
        contents = json.loads(path.read_text(encoding="utf-8"))
        resources.append((f"{BASE}schemas/{path.name}", Resource.from_contents(contents, DRAFT202012)))
    openapi = yaml.safe_load((spec_dir / "openapi-intel.yaml").read_text(encoding="utf-8"))
    resources.append((f"{BASE}openapi-intel.yaml", DRAFT202012.create_resource(openapi)))
    return Registry().with_resources(resources)


def validate_spec(ref: str, instance: Any) -> None:
    """ref: 'schemas/Snapshot.json' or 'openapi-intel.yaml#/components/schemas/Epoch'."""
    Draft202012Validator({"$ref": BASE + ref}, registry=_registry()).validate(instance)


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def rfc3339(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def raw_pub(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


@dataclass
class Keys:
    bank: Ed25519PrivateKey
    snapshot: Ed25519PrivateKey


@pytest.fixture(autouse=True)
def throwaway_keys(tmp_path: Path, settings: Any) -> Keys:
    """Never the shared dev keys: fresh bank attestation and snapshot keys per test."""
    bank, snap = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    (tmp_path / "bank.pub").write_text(b64(raw_pub(bank)) + "\n", encoding="ascii")
    pem = snap.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    (tmp_path / "snap.pem").write_bytes(pem)
    settings.SAFEPAY_BANK_ATTESTATION_PUB_FILE = tmp_path / "bank.pub"
    settings.SAFEPAY_BANK_ATTESTATION_KEY_ID = "bank:attestation:1"
    settings.SAFEPAY_INTEL_SNAPSHOT_KEY_FILE = tmp_path / "snap.pem"
    settings.SAFEPAY_INTEL_SNAPSHOT_KEY_ID = "intel:snapshot:1"
    return Keys(bank=bank, snapshot=snap)


# A few valid (mod-97) synthetic IBANs.
PAYEE_IBAN = "GB82WEST12345698765432"
OTHER_PAYEE_IBAN = "DE89370400440532013000"


@dataclass
class MemberHandle:
    member: Member
    key: Ed25519PrivateKey
    key_id: str
    account_id: str
    iban: str
    client: DjangoClient = field(default_factory=Client)

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        timestamp: int | None = None,
        nonce: str | None = None,
        sign_key: Ed25519PrivateKey | None = None,
        key_id: str | None = None,
    ) -> Any:
        raw = b"" if body is None else json.dumps(body).encode("utf-8")
        ts = str(int(datetime.now(UTC).timestamp()) if timestamp is None else timestamp)
        n = nonce or secrets.token_hex(16)
        msg = request_signing_string(method, path, ts, n, raw)
        sig = b64((sign_key or self.key).sign(msg))
        headers = {
            "X-SafePay-Key-Id": key_id or self.key_id,
            "X-SafePay-Timestamp": ts,
            "X-SafePay-Nonce": n,
            "X-SafePay-Signature": sig,
        }
        return self.client.generic(method, path, data=raw, content_type="application/json", headers=headers)

    def report(self, receipt: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "report_id": str(uuid.uuid4()),
            "member_id": str(self.member.id),
            "payee": {"country": receipt["creditor"]["country"], "iban": receipt["creditor"]["iban"]},
            "category": "INVOICE_REDIRECTION",
            "receipt": receipt,
            "evidence_sha256": "3" * 64,
            "created_at": rfc3339(datetime.now(UTC)),
            "nonce": secrets.token_hex(16),
            "key_id": self.key_id,
        }
        body.update(overrides)
        body["sig"] = sign_object(self.key, body)
        return body


_IBANS = [
    "GB33BUKB20201555555555",
    "GB94BARC10201530093459",
    "GB29NWBK60161331926819",
    "FR1420041010050500013M02606",
]


@pytest.fixture
def make_member(db: None) -> Callable[..., MemberHandle]:
    counter = iter(range(1000))

    def _make(name: str = "Twin SME (SYNTHETIC)") -> MemberHandle:
        i = next(counter)
        member = Member.objects.create(name=name)
        key = Ed25519PrivateKey.generate()
        key_id = f"member-{member.id.hex[:8]}:1"
        MemberKey.objects.create(key_id=key_id, member=member, public_key=raw_pub(key))
        account_id = str(uuid.uuid4())
        iban = _IBANS[i % len(_IBANS)]
        MemberAccount.objects.create(account_id=account_id, member=member, iban=iban)
        return MemberHandle(member=member, key=key, key_id=key_id, account_id=account_id, iban=iban)

    return _make


@pytest.fixture
def make_receipt(throwaway_keys: Keys) -> Callable[..., dict[str, Any]]:
    def _make(
        payer: MemberHandle,
        *,
        payee_iban: str = PAYEE_IBAN,
        booked_at: datetime | None = None,
        sign_key: Ed25519PrivateKey | None = None,
        **overrides: Any,
    ) -> dict[str, Any]:
        receipt: dict[str, Any] = {
            "receipt_id": str(uuid.uuid4()),
            "transfer_id": str(uuid.uuid4()),
            "end_to_end_id": str(uuid.uuid4()),
            "status": "SETTLED",
            "debtor": {"account_id": payer.account_id, "iban": payer.iban},
            "creditor": {"country": payee_iban[:2], "iban": payee_iban, "name": "Acme Supp1ies Ltd"},
            "amount_minor": 125000,
            "currency": "USD",
            "remittance_info": "INV-1042",
            "booked_at": rfc3339(booked_at or datetime.now(UTC) - timedelta(days=1)),
            "failure_reason": None,
            "bank_id": "SAFEPAY-SANDBOX",
            "simulated": True,
            "key_id": "bank:attestation:1",
        }
        receipt.update(overrides)
        receipt["sig"] = sign_object(sign_key or throwaway_keys.bank, receipt)
        return receipt

    return _make


def problem(response: Any, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.content
    assert response["Content-Type"] == "application/problem+json"
    body: dict[str, Any] = json.loads(response.content)
    assert body["code"] == code
    assert body["type"] == f"urn:safepay:problem:{code}"
    validate_spec("schemas/Problem.json", body)
    return body
