"""SafePay harness CLI (M10). Everything it creates is SYNTHETIC; the bank is SIMULATED.

  python -m safepay_sim.harness seed            [--seed 42]   live stack: 8 twin tenants, users, registry
  python -m safepay_sim.harness otp USERNAME                  current one-time code of a seeded user (dev)
  python -m safepay_sim.harness demo                          scripted walkthrough on the live stack
  python -m safepay_sim.harness evidence        [--seed 42]   B0/B1/B2 run into evidence/run-<seed>/
  python -m safepay_sim.harness verify-evidence [--seed 42]   offline re-verification of the bundle
  python -m safepay_sim.harness readme          [--seed 42]   regenerate README.md's results table
  python -m safepay_sim.harness verify-audit                  recompute every live tenant's audit chain

Set SAFEPAY_RUNNER=native to run against a local stack (ops/scripts/native_stack.py) instead of docker.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.twofactor.totp import TOTP

from safepay_sim.twins.__main__ import main as twins_main

from . import metrics as metrics_module
from .plan import bank_accounts, make_plan
from .report import readme_status, render, sync_readme
from .runner import REPO, Runner
from .verify import EvidenceError, verify_bundle

CREDENTIALS = REPO / "ops" / "dev-keys" / "demo" / "credentials.json"
README = REPO / "README.md"


def twins(seed: int) -> Path:
    out = REPO / "evidence" / f"twins-{seed}"
    if not (out / "labels.json").exists() and twins_main(["--seed", str(seed), "--out", str(out)]) != 0:
        raise SystemExit("twin generation failed")
    return out


def register(runner: Runner, accounts_file: Path, members: list[dict[str, Any]]) -> None:
    runner.manage("bank", ["seed_bank_accounts", "--file", runner.path(accounts_file)])
    for member in members:
        args = [
            "create_member",
            "--name",
            member["name"],
            "--member-id",
            member["member_id"],
            "--key-id",
            member["key_id"],
            "--public-key",
            member["public_key_b64"],
        ]
        for account in member["accounts"]:
            args += ["--account", account]
        runner.manage("intel", args)


def cmd_seed(options: argparse.Namespace) -> int:
    runner = Runner.detect()
    if CREDENTIALS.exists() and not options.force:
        print(f"already seeded ({CREDENTIALS}); use --force after resetting the database")
        return 0
    source = twins(options.seed)
    document = runner.manage(
        "core", ["seed_twins", "--twins-dir", runner.path(source), "--out", "-"], live=True
    )
    data = json.loads(document[document.index("{") :])
    CREDENTIALS.parent.mkdir(parents=True, exist_ok=True)
    CREDENTIALS.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    if os.name != "nt":
        CREDENTIALS.chmod(0o600)
    seeded = data["tenants"]
    accounts_file = CREDENTIALS.parent / "bank-accounts.json"
    accounts_file.write_text(
        json.dumps(
            [
                {
                    "account_id": t["bank_account_id"],
                    "iban": t["bank_iban"],
                    "owner_name": f"{t['company']} (SYNTHETIC)",
                    "currency": "USD",
                    "opening_balance_minor": t["closing_balance_minor"],
                }
                for t in seeded
            ]
            + [
                {
                    "account_id": t["legacy_account"]["account_id"],
                    "iban": t["legacy_account"]["iban"],
                    "owner_name": f"{t['company']} legacy account (SYNTHETIC)",
                    "currency": "USD",
                    "opening_balance_minor": 1_000_000,
                }
                for t in seeded
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    members = [
        {
            "name": f"{t['company']} (SYNTHETIC)",
            "member_id": t["tenant_id"],
            "key_id": t["member"]["key_id"],
            "public_key_b64": t["member"]["public_key_b64"],
            "accounts": [
                f"{t['bank_account_id']}:{t['bank_iban']}",
                f"{t['legacy_account']['account_id']}:{t['legacy_account']['iban']}",
            ],
        }
        for t in seeded
    ]
    register(runner, accounts_file, members)
    print(f"{len(seeded)} SYNTHETIC tenants seeded; bank accounts opened; network members admitted.")
    print("Sign in to the console with one of these users (passwords in the credentials file):")
    for t in seeded:
        print("  " + ", ".join(u["username"] for u in t["users"]))
    print(f"Credentials (dev only, gitignored): {CREDENTIALS}")
    print("One-time code for a user: python -m safepay_sim.harness otp <username>")
    return 0


def find_user(username: str) -> dict[str, Any]:
    for tenant in json.loads(CREDENTIALS.read_text(encoding="utf-8"))["tenants"]:
        for user in tenant["users"]:
            if user["username"] == username:
                return {**user, "tenant": tenant}
    raise SystemExit(f"{username} is not in {CREDENTIALS}")


def totp(key_hex: str) -> str:
    return TOTP(bytes.fromhex(key_hex), 6, hashes.SHA1(), 30).generate(time.time()).decode()  # noqa: S303


def cmd_otp(options: argparse.Namespace) -> int:
    user = find_user(options.username)
    if options.password:
        print(user["password"])
    print(totp(user["totp_key_hex"]))
    return 0


def cmd_evidence(options: argparse.Namespace) -> int:
    runner = Runner.detect()
    seed = options.seed
    source = twins(seed)
    run_dir = REPO / "evidence" / f"run-{seed}"
    keys_dir = REPO / "ops" / "dev-keys" / "evidence" / f"run-{seed}"
    for path in (run_dir, keys_dir):
        if path.exists():
            shutil.rmtree(path)
    keys_dir.mkdir(parents=True)
    plan = make_plan(seed, source, keys_dir, dt.datetime.now(dt.UTC).date())
    (keys_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    core_plan = {
        **plan,
        "twins_dir": runner.path(source),
        "attacker_member": {
            **plan["attacker_member"],
            "key_file": runner.path(Path(plan["attacker_member"]["key_file"])),
        },
    }
    core_plan_file = keys_dir / "plan.core.json"
    core_plan_file.write_text(json.dumps(core_plan, indent=2), encoding="utf-8")
    accounts_file = keys_dir / "bank-accounts.json"
    accounts_file.write_text(json.dumps(bank_accounts(plan), indent=2), encoding="utf-8")
    tenants, members = len(plan["tenants"]), len(plan["members"])
    print(f"evidence {plan['run_id']}: registering {tenants} bank accounts and {members} members")
    register(runner, accounts_file, plan["members"])
    print("running scenarios (core in-process, simulated clock; bank and intel over signed HTTP)...")
    output = runner.manage(
        "core",
        ["run_evidence", "--plan", runner.path(core_plan_file), "--out", runner.path(run_dir)],
        settings="evidence",
        extra_env={
            "SAFEPAY_KEYSTORE_DIR": runner.path(keys_dir / "keystore"),
            "SAFEPAY_MEMBER_KEYS_DIR": runner.path(keys_dir / "members"),
            "SAFEPAY_EVIDENCE_DB": f"safepay_core_evidence_{seed}",
        },
    )
    print(output.strip().splitlines()[-1] if output.strip() else "run_evidence produced no output")
    return finalise(run_dir)


def finalise(run_dir: Path) -> int:
    records = [
        json.loads(line) for line in (run_dir / "ops.jsonl").read_text(encoding="utf-8").splitlines() if line
    ]
    labels = json.loads((run_dir / "labels.json").read_text(encoding="utf-8"))
    run = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    privacy = json.loads((run_dir / "privacy.json").read_text(encoding="utf-8"))
    metrics = metrics_module.compute(records, labels, run, privacy)
    (run_dir / "metrics.json").write_text(metrics_module.dumps(metrics), encoding="utf-8")
    verified = verify_bundle(run_dir, run_dir / "labels.json")
    (run_dir / "EVIDENCE.md").write_text(render(metrics, run, verified), encoding="utf-8")
    if sync_readme(README, metrics):
        print(f"README.md results table regenerated from {run_dir.name}/metrics.json")
    summary = {
        k: {m: v[m] for m in ("executed", "passed", "attacker_loss_minor")}
        for k, v in metrics["configs"].items()
    }
    print(
        json.dumps({"bundle": str(run_dir), "configs": summary, "verified": verified["verified"]}, indent=2)
    )
    return 0


def cmd_verify_evidence(options: argparse.Namespace) -> int:
    run_dir = REPO / "evidence" / f"run-{options.seed}"
    if not (run_dir / "ops.jsonl").exists():
        print(f"no bundle at {run_dir}; run make evidence SEED={options.seed} first", file=sys.stderr)
        return 2
    try:
        result = verify_bundle(run_dir, run_dir / "labels.json")
    except EvidenceError as exc:
        print(f"verify-evidence FAILED: {exc}", file=sys.stderr)
        return 1
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    result["readme"] = readme_status(README, metrics)
    print(json.dumps(result, indent=2))
    if result["readme"] == "stale":
        print(
            "verify-evidence FAILED: README.md shows other numbers for this run; "
            f"refresh it with: cd sim && uv run python -m safepay_sim.harness readme --seed {options.seed}",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_readme(options: argparse.Namespace) -> int:
    """Regenerate the README results table from an existing, verified bundle."""
    run_dir = REPO / "evidence" / f"run-{options.seed}"
    try:
        verify_bundle(run_dir, run_dir / "labels.json")
    except (EvidenceError, OSError) as exc:
        print(f"bundle {run_dir} does not verify: {exc}", file=sys.stderr)
        return 1
    metrics = json.loads((run_dir / "metrics.json").read_text(encoding="utf-8"))
    if not sync_readme(README, metrics):
        print("README.md has no results markers", file=sys.stderr)
        return 1
    print(f"README.md results table regenerated from {run_dir.name}/metrics.json")
    return 0


def cmd_verify_audit(options: argparse.Namespace) -> int:
    runner = Runner.detect()
    if not CREDENTIALS.exists():
        print("no seeded tenants (run make seed first)", file=sys.stderr)
        return 2
    for tenant in json.loads(CREDENTIALS.read_text(encoding="utf-8"))["tenants"]:
        print(runner.manage("core", ["verify_audit", "--tenant", tenant["tenant_id"]], live=True).strip())
    return 0


def cmd_demo(options: argparse.Namespace) -> int:
    from .demo import run_demo  # noqa: PLC0415

    return run_demo(Runner.detect(), CREDENTIALS, options)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m safepay_sim.harness", description=__doc__.splitlines()[0]
    )
    sub = parser.add_subparsers(dest="command", required=True)
    seed = sub.add_parser("seed")
    seed.add_argument("--seed", type=int, default=42)
    seed.add_argument("--force", action="store_true")
    otp = sub.add_parser("otp")
    otp.add_argument("username")
    otp.add_argument("--password", action="store_true", help="also print the password")
    demo = sub.add_parser("demo")
    demo.add_argument("--core", default="http://127.0.0.1:8000")
    for name in ("evidence", "verify-evidence", "readme"):
        sub.add_parser(name).add_argument("--seed", type=int, default=42)
    sub.add_parser("verify-audit")
    options = parser.parse_args(argv)
    handlers = {
        "seed": cmd_seed,
        "otp": cmd_otp,
        "demo": cmd_demo,
        "evidence": cmd_evidence,
        "verify-evidence": cmd_verify_evidence,
        "readme": cmd_readme,
        "verify-audit": cmd_verify_audit,
    }
    return handlers[options.command](options)


if __name__ == "__main__":
    sys.exit(main())
