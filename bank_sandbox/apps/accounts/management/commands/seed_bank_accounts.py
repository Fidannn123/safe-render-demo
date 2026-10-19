"""Seed SYNTHETIC bank accounts into the SIMULATED bank (idempotent).

Without --file: one deterministic demo account per twin sector, derived from --seed.
With --file: a JSON list of {account_id, iban, owner_name, currency?, opening_balance_minor?}
(for example produced by the M5 twins). Opening balances are booked as funding CREDIT lines.
"""

from __future__ import annotations

import json
import random
import uuid
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from apps.accounts.iban import make_iban
from apps.accounts.services import open_account

SECTORS = (
    "retail",
    "construction",
    "restaurant",
    "logistics",
    "pharmacy",
    "agriculture",
    "IT services",
    "textiles",
)
SEED_NAMESPACE = uuid.UUID("5afe9a7e-0000-4000-8000-0000000005ed")


def demo_accounts(seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)  # noqa: S311 (synthetic data, not security)
    accounts = []
    for i, sector in enumerate(SECTORS, start=1):
        bban = f"SPSB{rng.randrange(10**13, 10**14):014d}"
        accounts.append(
            {
                "account_id": str(uuid.uuid5(SEED_NAMESPACE, f"{seed}:{sector}")),
                "iban": make_iban("GB", bban),
                "owner_name": f"Twin SME {i} {sector} (SYNTHETIC)",
                "currency": "USD",
                "opening_balance_minor": rng.randrange(20_000, 200_000) * 100,
            }
        )
    return accounts


class Command(BaseCommand):
    help = "Seed SYNTHETIC accounts into the SIMULATED bank sandbox (idempotent)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--file", type=Path, default=None, help="JSON list of accounts")

    def handle(self, *args: Any, **options: Any) -> None:
        path: Path | None = options["file"]
        if path is not None:
            specs: list[dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
        else:
            specs = demo_accounts(options["seed"])
        for spec in specs:
            account = open_account(
                account_id=uuid.UUID(spec["account_id"]),
                iban=spec["iban"],
                owner_name=spec["owner_name"],
                currency=spec.get("currency", "USD"),
                opening_balance_minor=int(spec.get("opening_balance_minor", 0)),
            )
            self.stdout.write(
                f"SIMULATED account {account.account_id} {account.iban} balance={account.balance_minor}"
            )
        self.stdout.write(self.style.SUCCESS(f"seeded {len(specs)} SYNTHETIC accounts"))
