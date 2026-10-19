"""Shared fixtures: throwaway Ed25519 keys (never the shared dev keys), a signing client, spec validators."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings
from django.test import Client
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

from apps.accounts.iban import make_iban
from apps.accounts.models import Account
from apps.accounts.services import open_account
from safepay_bank import clock
from safepay_bank.reqsig import sign_request
from safepay_bank.signing import public_key_b64

CORE_KEY_ID = "core:service:test"
BANK_KEY_ID = "bank:attestation:test"
SCHEMA_BASE = "https://safepay.invalid/spec/"


class Keys:
    def __init__(self, directory: Path) -> None:
        self.bank = Ed25519PrivateKey.generate()
        self.core = Ed25519PrivateKey.generate()
        self.bank_pem = directory / "bank_attestation.pem"
        self.core_pub = directory / "core_service.pub"
        self.bank_pem.write_bytes(
            self.bank.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
        self.core_pub.write_text(public_key_b64(self.core.public_key()), encoding="ascii")

    @property
    def bank_public(self) -> Ed25519PublicKey:
        return self.bank.public_key()


@pytest.fixture(scope="session")
def keys(tmp_path_factory: pytest.TempPathFactory) -> Keys:
    return Keys(tmp_path_factory.mktemp("bank-test-keys"))


@pytest.fixture(autouse=True)
def _test_keys(settings: Any, keys: Keys) -> None:
    settings.SAFEPAY_BANK_ATTESTATION_KEY_FILE = keys.bank_pem
    settings.SAFEPAY_BANK_ATTESTATION_KEY_ID = BANK_KEY_ID
    settings.SAFEPAY_REQUEST_SIGNING_KEYS = {CORE_KEY_ID: keys.core_pub}
    settings.SAFEPAY_BANK_TIMEOUT_SECONDS = 0


class SignedClient:
    """Signs every request like core's bank adapter will (D-012)."""

    def __init__(self, client: Client, key: Ed25519PrivateKey, key_id: str = CORE_KEY_ID) -> None:
        self.client = client
        self.key = key
        self.key_id = key_id

    def headers(
        self,
        method: str,
        path: str,
        query: str = "",
        body: bytes = b"",
        *,
        ts: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        signed = sign_request(
            self.key,
            self.key_id,
            method=method,
            path=path,
            query=query,
            body=body,
            timestamp=int(clock.now().timestamp()) if ts is None else ts,
            nonce=nonce,
        )
        return signed.as_dict()

    def post(self, path: str, payload: Any, **kw: Any) -> Any:
        body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
        headers = self.headers("POST", path, "", body, **kw)
        return self.client.post(path, data=body, content_type="application/json", headers=headers)

    def get(self, path: str, query: str = "", **kw: Any) -> Any:
        headers = self.headers("GET", path, query, b"", **kw)
        return self.client.get(f"{path}?{query}" if query else path, headers=headers)


@pytest.fixture
def bank(client: Client, keys: Keys) -> SignedClient:
    return SignedClient(client, keys.core)


# ------------------------------------------------------------------ spec validation
def _registry() -> Registry:
    spec_dir: Path = settings.SAFEPAY_SPEC_DIR
    resources = [
        (
            SCHEMA_BASE + "schemas/" + p.name,
            Resource.from_contents(json.loads(p.read_text("utf-8")), DRAFT202012),
        )
        for p in sorted((spec_dir / "schemas").glob("*.json"))
    ]
    openapi = yaml.safe_load((spec_dir / "openapi-bank.yaml").read_text(encoding="utf-8"))
    for name, schema in openapi["components"]["schemas"].items():
        resources.append((SCHEMA_BASE + name, Resource.from_contents(schema, DRAFT202012)))
    return Registry().with_resources(resources)


@pytest.fixture(scope="session")
def validate() -> Any:
    registry = _registry()

    def _validate(name: str, instance: Any) -> None:
        uri = SCHEMA_BASE + (f"schemas/{name}" if name.endswith(".json") else name)
        Draft202012Validator({"$ref": uri}, registry=registry, format_checker=FormatChecker()).validate(
            instance
        )

    return _validate


# ------------------------------------------------------------------ data
def new_account(
    balance: int = 1_000_000, name: str = "Test SME (SYNTHETIC)", currency: str = "USD"
) -> Account:
    account_id = uuid.uuid4()
    iban = make_iban("GB", f"SPSB{account_id.int % 10**14:014d}")
    return open_account(
        account_id=account_id, iban=iban, owner_name=name, currency=currency, opening_balance_minor=balance
    )


VENDOR_IBAN = "GB82WEST12345698765432"  # valid mod-97 (spec example)


def transfer_request(debtor: Account, amount: int = 125_000, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "end_to_end_id": str(uuid.uuid4()),
        "debtor_account_id": str(debtor.account_id),
        "creditor": {"country": "GB", "iban": VENDOR_IBAN, "name": "Acme Supplies Ltd (SYNTHETIC)"},
        "amount_minor": amount,
        "currency": "USD",
        "remittance_info": "INV-1042",
    }
    body.update(overrides)
    return body


@pytest.fixture
def account(db: None) -> Account:
    return new_account()
