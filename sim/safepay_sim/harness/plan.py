"""Evidence run plan: one tenant per (configuration, twin), its SIMULATED bank account and network key.

Tenant ids, bank accounts and member keys are fresh for every run (the intel network and the bank are
shared services that keep state between runs). Private keys go to the gitignored key directory, never
into the evidence bundle.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import secrets
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

CONFIGS = ("B0", "B1", "B2")


def iban(country: str, bban: str) -> str:
    digits = "".join(str(int(char, 36)) for char in bban + country + "00")
    return f"{country}{98 - int(digits) % 97:02d}{bban}"


def fresh_iban() -> str:
    return iban("GB", "SAFE" + "".join(str(secrets.randbelow(10)) for _ in range(14)))


def write_key(path: Path) -> str:
    """Create an Ed25519 key file; return the raw public key, base64."""
    key = Ed25519PrivateKey.generate()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    return base64.b64encode(key.public_key().public_bytes_raw()).decode("ascii")


def make_plan(seed: int, twins_dir: Path, keys_dir: Path, as_of: dt.date) -> dict[str, Any]:
    labels = json.loads((twins_dir / "labels.json").read_text(encoding="utf-8"))
    tenants, members = [], []
    for config in CONFIGS:
        for twin in sorted(t["twin_key"] for t in labels["twins"]):
            ledger = json.loads((twins_dir / "twins" / twin / "ledger.json").read_text(encoding="utf-8"))
            twin_json = json.loads((twins_dir / "twins" / twin / "twin.json").read_text(encoding="utf-8"))
            row = {
                "config": config,
                "twin_key": twin,
                "company": twin_json["company"],
                "tenant_id": str(uuid.uuid4()),
                "bank_account_id": str(uuid.uuid4()),
                "bank_iban": fresh_iban(),
                "opening_balance_minor": int(ledger["closing_bank_balance_minor"]),
            }
            tenants.append(row)
            if config != "B0":  # B0 has no core tenant and no network membership
                key_id = f"member-{row['tenant_id']}:1"
                public = write_key(keys_dir / "members" / f"{row['tenant_id']}.pem")
                members.append(
                    {
                        "name": f"{twin_json['company']} [{config}] (SYNTHETIC)",
                        "member_id": row["tenant_id"],
                        "key_id": key_id,
                        "public_key_b64": public,
                        "accounts": [f"{row['bank_account_id']}:{row['bank_iban']}"],
                    }
                )
    attacker_id = str(uuid.uuid4())
    attacker_file = keys_dir / "attacker-member.pem"
    attacker = {
        "name": "Enumerating member (SYNTHETIC attacker)",
        "member_id": attacker_id,
        "key_id": f"member-{attacker_id}:1",
        "public_key_b64": write_key(attacker_file),
        "accounts": [],
    }
    members.append(attacker)
    return {
        "seed": seed,
        "run_id": f"run-{seed}",
        "as_of": as_of.isoformat(),
        "twins_dir": str(twins_dir),
        "tenants": tenants,
        "members": members,
        "attacker_member": {"key_id": attacker["key_id"], "key_file": str(attacker_file)},
        "synthetic": True,
    }


def bank_accounts(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "account_id": row["bank_account_id"],
            "iban": row["bank_iban"],
            "owner_name": f"{row['company']} [{row['config']} {plan['run_id']}] (SYNTHETIC)",
            "currency": "USD",
            "opening_balance_minor": row["opening_balance_minor"],
        }
        for row in plan["tenants"]
    ]
