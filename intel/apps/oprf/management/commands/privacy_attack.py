"""Run the operator-side privacy attack on an evidence bundle (apps/oprf/adversary.py).

  python manage.py privacy_attack --run-dir evidence/run-42 [--candidates 1000000]

Reads privacy-transcript.jsonl (every captured intel request) and privacy-candidates.json (the true
payees) that `run_evidence` writes, loads the epoch keys from this intel database (the adversary is the
operator), and writes privacy-attack.json. SYNTHETIC data only.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.oprf.adversary import Payee, attack, naive_control, parse_transcript, synthetic_candidates
from apps.oprf.models import EpochKey
from apps.oprf.port import OprfServer
from safepay_intel.crypto import b64decode_strict
from safepay_intel.policy import network_policy

CONTROL_SYNTHETIC = 10_000


class Command(BaseCommand):
    help = "Operator-side privacy attack on a run's blind-lookup transcript (writes privacy-attack.json)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--run-dir", required=True)
        parser.add_argument("--candidates", type=int, default=1_000_000)

    def handle(self, *args: object, **options: object) -> None:
        run_dir = Path(str(options["run_dir"]))
        total = int(str(options["candidates"]))
        lines = [
            json.loads(raw)["request"]
            for raw in (run_dir / "privacy-transcript.jsonl").read_text(encoding="utf-8").splitlines()
            if raw.strip()
        ]
        true = [
            Payee(p["country"], p["iban"])
            for p in json.loads((run_dir / "privacy-candidates.json").read_text())
        ]
        if not true or total < len(true):
            raise CommandError("need the true payees and at least as many candidates")
        queries = parse_transcript(lines, b64decode_strict)
        keys = {
            int(k.epoch): OprfServer(bytes(k.seed), bytes(k.key_info))
            for k in EpochKey.objects.filter(epoch__in={q.epoch for q in queries})
        }
        missing = {q.epoch for q in queries} - set(keys)
        if missing:
            raise CommandError(f"epoch keys missing from this intel database: {sorted(missing)}")
        domain = network_policy().input_domain
        exclude = {p.iban for p in true}

        def candidates(extra: int) -> list[Payee]:
            return true + list(synthetic_candidates(true, extra, set(exclude)))

        started = time.monotonic()
        result = attack(queries, candidates(total - len(true)), keys, domain)
        control = naive_control(true, candidates(CONTROL_SYNTHETIC), keys, domain)
        report = {
            "adversary": "network operator: full request transcript + every epoch OPRF key + candidate list",
            "epochs": sorted(keys),
            "true_payees": len(true),
            "attack": result,
            "control_naive_sha256_lookup": control,
            "chance_rate": f"1/{result['candidates_checked']}",
            "seconds": round(time.monotonic() - started, 1),
            "passed": result["recovered_identifiers"] == 0
            and result["repeated_blinded_elements"] == 0
            and control["recovered_identifiers"] == len(true),
            "synthetic": True,
        }
        (run_dir / "privacy-attack.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        self.stdout.write(json.dumps(report, sort_keys=True))
        if not report["passed"]:
            raise CommandError("privacy attack recovered identifiers or the control failed")
