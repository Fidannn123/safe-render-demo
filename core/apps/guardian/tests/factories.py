"""Builders for GuardianInputs used by the guardian tests (SYNTHETIC data)."""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from apps.guardian.inputs import GuardianInputs

NOW = dt.datetime(2026, 10, 19, 8, 0, 0, tzinfo=dt.UTC)
TENANT = "11111111-2222-4333-8444-555555555555"
PAYMENT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
INVOICE = "7d3f1c2a-0b1e-4c55-9a8e-1f2d3c4b5a69"
VENDOR = "2b8f6a1e-3c4d-4e5f-8a9b-0c1d2e3f4a5b"
ACCOUNT = "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d"
OTHER_VENDOR = "3c9f7b2f-4d5e-4f60-9bac-1d2e3f4a5b6c"
OTHER_ACCOUNT = "0b1c2d3e-4f50-4617-8293-a4b5c6d7e8f9"
IBAN = "GB82WEST12345698765432"
OTHER_IBAN = "DE89370400440532013000"
FINGERPRINT = "1" * 64


def iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def ago(**kwargs: float) -> str:
    return iso(NOW - dt.timedelta(**kwargs))


def base() -> dict[str, Any]:
    """Known vendor, verified account used for 180 days, clean everything: ALLOW / T1 / score 0."""
    return {
        "tenant_id": TENANT,
        "proposal": {
            "payment_id": PAYMENT,
            "invoice_id": INVOICE,
            "vendor_id": VENDOR,
            "account_id": ACCOUNT,
            "amount_minor": 125000,
            "currency": "USD",
            "reference": "INV-1042",
            "due_date": "2026-11-01",
            "invoice_fingerprint": FINGERPRINT,
        },
        "vendor": {"vendor_id": VENDOR, "name": "Acme Supplies Ltd", "blocked": False},
        "registry_account": {
            "account_id": ACCOUNT,
            "vendor_id": VENDOR,
            "country": "GB",
            "iban": IBAN,
            "changed_at": ago(days=200),
            "first_used_at": ago(days=180),
        },
        "known_vendors": [
            {
                "vendor_id": OTHER_VENDOR,
                "name": "Northwind Logistics GmbH",
                "accounts": [{"account_id": OTHER_ACCOUNT, "iban": OTHER_IBAN}],
            }
        ],
        "extraction": {
            "vendor_name": "Acme Supplies Ltd",
            "reference": "INV-1042",
            "amount_minor": 125000,
            "currency": "USD",
            "bank_details_on_invoice": {"country": "GB", "iban": IBAN},
            "untrusted_text": [
                {"field": "memo", "value": "Thank you for your business.", "provenance": "untrusted"}
            ],
        },
        "history": [],
        "rolling": {"outflow_24h_minor": 0, "new_payee_outflow_24h_minor": 0},
        "forecast": {"buffer_minor": 500000, "projected_min_balance_minor": 2000000},
        "network": {"status": "SKIP", "reporters": 0, "epoch": None},
        "tenant_frozen": False,
        "now": iso(NOW),
    }


def new_payee(d: dict[str, Any]) -> dict[str, Any]:
    """Make the payee account new (never used, added 3 days ago) with a clean network MISS."""
    d["registry_account"]["changed_at"] = ago(days=3)
    d["registry_account"]["first_used_at"] = None
    d["network"] = {"status": "MISS", "reporters": 0, "epoch": 7}
    return d


def history_item(n: int, **overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "payment_id": f"00000000-0000-4000-8000-{n:012d}",
        "vendor_id": VENDOR,
        "account_id": ACCOUNT,
        "amount_minor": 120000,
        "currency": "USD",
        "reference": f"OLD-{n:04d}",
        "invoice_fingerprint": f"{n:064x}",
        "state": "RECONCILED",
        "created_at": ago(days=40 + n),
    }
    item.update(overrides)
    return item


def build(d: dict[str, Any]) -> GuardianInputs:
    return GuardianInputs.model_validate(copy.deepcopy(d))
