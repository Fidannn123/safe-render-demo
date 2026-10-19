"""Core side of `make evidence`: fresh database, B0/B1/B2 scenario run, signed artifacts. SYNTHETIC only.

Use with DJANGO_SETTINGS_MODULE=safepay_core.settings.evidence. The plan (tenants, bank accounts, member
keys) is written by `python -m safepay_sim.harness`, which also registers the bank accounts and network
members before calling this command. The evidence database is recreated on every run and kept afterwards
for inspection; the live database is never touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connection

from apps.evidence.harness import Harness


class Command(BaseCommand):
    help = "Run the B0/B1/B2 evidence scenarios against a fresh database (SYNTHETIC data, SIMULATED bank)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--plan", type=Path, required=True)
        parser.add_argument("--out", type=Path, required=True)

    def handle(self, *args: Any, **options: Any) -> None:
        if not settings.SETTINGS_MODULE.endswith(".evidence"):
            raise CommandError("run with DJANGO_SETTINGS_MODULE=safepay_core.settings.evidence")
        plan = json.loads(options["plan"].read_text(encoding="utf-8"))
        name = connection.creation.create_test_db(verbosity=0, autoclobber=True, serialize=False)
        self.stdout.write(f"evidence database {name} created and migrated (owner role)")
        # The run itself uses the least-privilege runtime role, exactly as core-web does.
        connection.close()
        connection.settings_dict["USER"] = settings.SAFEPAY_DB_APP_ROLE
        connection.settings_dict["PASSWORD"] = settings.SAFEPAY_DB_APP_PASSWORD
        harness = Harness(plan, options["out"], lambda line: self.stdout.write(line))
        harness.run()
        self.stdout.write(self.style.SUCCESS(f"{len(harness.records)} evidence records in {options['out']}"))
