"""Seed SYNTHETIC digital twins into the live core database (make seed). Dev/demo only.

Reads the output of `python -m safepay_sim.twins --seed N --out DIR`. For every twin it creates a tenant
with owner/approver/accountant users (TOTP enrolled), a verified vendor registry, receivables, recurring
opex and 90 days of ledger history, mapped so that the twin's as_of date is today. It also creates the
tenant's intel member key. Credentials and public keys go to --out (gitignored dev-keys), never to stdout.
"""

from __future__ import annotations

import base64
import datetime as dt
import json
import uuid
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.utils import timezone

from apps.accounts.models import User
from apps.evidence.seeding import DayMap, legacy_account_id, seed_twin, synthetic_iban
from apps.intel_client.service import member_key_path


def member_key(tenant_id: uuid.UUID) -> dict[str, str]:
    """Create (once) the tenant's Ed25519 network-member key; return what intel must admit."""
    path = member_key_path(tenant_id)
    if path.exists():
        private = serialization.load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(private, Ed25519PrivateKey):
            raise CommandError(f"{path} is not an Ed25519 key")
    else:
        private = Ed25519PrivateKey.generate()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            private.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
            )
        )
    raw = private.public_key().public_bytes_raw()
    return {"key_id": f"member-{tenant_id}:1", "public_key_b64": base64.b64encode(raw).decode("ascii")}


class Command(BaseCommand):
    help = "Seed SYNTHETIC twins (tenants, users, registry, 90-day history) into core. Dev/demo only."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--twins-dir", type=Path, required=True, help="safepay_sim.twins output")
        parser.add_argument(
            "--out", required=True, help="credentials JSON file, or - for stdout (keep it private)"
        )
        parser.add_argument("--only", default="", help="comma-separated twin keys (default: all)")
        parser.add_argument("--prefix", default="", help="username prefix (default: twin key)")
        parser.add_argument("--as-of", default="", help="real date for the twin's as_of (default today)")

    def handle(self, *args: Any, **options: Any) -> None:
        directory: Path = options["twins_dir"]
        labels = json.loads((directory / "labels.json").read_text(encoding="utf-8"))
        only = {key for key in str(options["only"]).split(",") if key}
        real_as_of = dt.date.fromisoformat(options["as_of"]) if options["as_of"] else timezone.localdate()
        days = DayMap(dt.date.fromisoformat(labels["as_of"]), real_as_of)
        seeded = []
        progress = self.stderr if options["out"] == "-" else self.stdout
        for twin_dir in sorted((directory / "twins").iterdir()):
            if only and twin_dir.name not in only:
                continue
            twin = json.loads((twin_dir / "twin.json").read_text(encoding="utf-8"))
            ledger = json.loads((twin_dir / "ledger.json").read_text(encoding="utf-8"))
            prefix = f"{options['prefix']}{twin['twin_key']}".replace("_", "-")
            if User.objects.filter(username__startswith=f"{prefix}.").exists():
                raise CommandError(f"{prefix} is already seeded; reset the database first")
            tenant_id = uuid.uuid4()
            result = seed_twin(twin, ledger, tenant_id=tenant_id, days=days, username_prefix=prefix)
            entry = result.as_dict() | {
                "member": member_key(tenant_id),
                "legacy_account": {"account_id": str(legacy_account_id(tenant_id)), "iban": synthetic_iban()},
                "as_of": real_as_of.isoformat(),
            }
            seeded.append(entry)
            progress.write(f"seeded {twin['twin_key']}: tenant {tenant_id} ({result.company}, SYNTHETIC)")
        document = json.dumps({"synthetic": True, "tenants": seeded}, indent=2) + "\n"
        if options["out"] == "-":
            # The harness writes the file on the host: a container user may not write host directories.
            self.stdout.write(document, ending="")
            return
        out = Path(options["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(document, encoding="utf-8")
        self.stdout.write(self.style.SUCCESS(f"{len(seeded)} SYNTHETIC tenants; credentials in {out}"))
