"""Print how chosen scenarios ended under B0, B1 and B2, straight from a run's ops.jsonl (for the demo video).

    uv run --frozen python ops/scripts/show_scenarios.py retail-swap-01 retail-hidden-01 [--seed 42]

SYNTHETIC data, SIMULATED bank. Nothing here is computed beyond reading the generated records.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenarios", nargs="+")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    path = REPO / "evidence" / f"run-{args.seed}" / "ops.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} not found: run `make evidence SEED={args.seed}` first")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for scenario in args.scenarios:
        for row in (r for r in rows if r["scenario_id"] == scenario):
            observed = row["observed"]
            verdict = observed.get("verdict") or observed.get("outcome", "")
            reasons = ", ".join(observed.get("reason_codes", []))
            loss = row["loss_minor"] / 100
            print(
                f"{row['config']}  {scenario:<28} {verdict:<7} {observed.get('tier') or '':<3} "
                f"loss USD {loss:>10,.2f}  {reasons}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
