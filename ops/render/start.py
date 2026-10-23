"""Run the disposable Render demo; all data and keys stay in /tmp/safepay.

The original Compose deployment remains the persistent, separated-service option.
This launcher retains its database roles and runs only synthetic data with mock LLM.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
# Created in the image and owned by its non-root user, inside the isolated container.
STATE = Path("/tmp/safepay")  # noqa: S108
sys.path.insert(0, str(REPO / "ops" / "scripts"))


def settings() -> tuple[str, str]:
    """Refuse incomplete demo credentials and invalid public port before starting."""
    password = os.environ.get("SAFEPAY_DEMO_PASSWORD", "")
    key = os.environ.get("SAFEPAY_DEMO_TOTP_KEY", "")
    if len(password) < 16 or len(key) != 40:
        raise SystemExit(
            "Set SAFEPAY_DEMO_PASSWORD (16+ characters) and SAFEPAY_DEMO_TOTP_KEY (40 hex digits)"
        )
    try:
        bytes.fromhex(key)
        port = int(os.environ.get("PORT", "10000"))
    except ValueError:
        raise SystemExit("Invalid demo TOTP key or PORT") from None
    if not 1024 <= port <= 65535 or len(bytes.fromhex(key)) != 20:
        raise SystemExit("PORT must be 1024..65535 and the TOTP key must encode 20 bytes")
    return password, key


def native() -> Any:
    return importlib.import_module("native_stack")


def run(command: list[str], **kwargs: Any) -> None:
    subprocess.run(command, check=True, **kwargs)  # noqa: S603


def seed() -> None:
    """Seed one complete SME, reusing the existing generator and registration paths."""
    sys.path.insert(0, str(REPO / "sim"))
    from safepay_sim.harness.__main__ import register, twins  # noqa: PLC0415
    from safepay_sim.harness.runner import Runner  # noqa: PLC0415

    class DemoRunner(Runner):
        def manage(self, service: str, args: list[str], **kwargs: Any) -> str:
            kwargs["extra_env"] = {**native().service_env(service), **kwargs.get("extra_env", {})}
            return super().manage(service, args, **kwargs)

    runner = DemoRunner("native", native().KEYS)
    source = twins(42)
    out = native().KEYS / "demo" / "credentials.json"
    if out.exists():
        return
    runner.manage(
        "core", ["seed_twins", "--only", "logistics", "--twins-dir", str(source), "--out", str(out)]
    )
    out.chmod(0o600)
    tenants = json.loads(out.read_text())["tenants"]
    accounts = []
    members = []
    for tenant in tenants:
        legacy = tenant["legacy_account"]
        accounts.extend(
            [
                {
                    "account_id": tenant["bank_account_id"],
                    "iban": tenant["bank_iban"],
                    "owner_name": tenant["company"] + " (SYNTHETIC)",
                    "currency": "USD",
                    "opening_balance_minor": tenant["closing_balance_minor"],
                },
                {
                    "account_id": legacy["account_id"],
                    "iban": legacy["iban"],
                    "owner_name": tenant["company"] + " legacy (SYNTHETIC)",
                    "currency": "USD",
                    "opening_balance_minor": 1_000_000,
                },
            ]
        )
        members.append(
            {
                "name": tenant["company"] + " (SYNTHETIC)",
                "member_id": tenant["tenant_id"],
                **tenant["member"],
                "accounts": [
                    f"{tenant['bank_account_id']}:{tenant['bank_iban']}",
                    f"{legacy['account_id']}:{legacy['iban']}",
                ],
            }
        )
    account_file = out.parent / "bank-accounts.json"
    account_file.write_text(json.dumps(accounts))
    register(runner, account_file, members)


def login() -> None:
    """Give the deploy owner a stable demo approver login without logging credentials."""
    password, key = settings()
    os.environ.update(native().service_env("core"))
    sys.path.insert(0, str(REPO / "core"))
    import django  # noqa: PLC0415

    django.setup()
    from django_otp.plugins.otp_totp.models import TOTPDevice  # noqa: PLC0415

    from apps.accounts.models import User  # noqa: PLC0415

    user = User.objects.get(username="logistics.approver")
    user.set_password(password)
    user.save(update_fields=["password"])
    TOTPDevice.objects.filter(user=user).update(key=key)
    print("Demo login configured for logistics.approver; use your private Render environment values.")


def start_postgres(spawn: Callable[..., None], children: list[subprocess.Popen[bytes]]) -> None:
    data = STATE / "pgdata"
    if not (data / "PG_VERSION").exists():
        run(
            [
                "initdb",
                "-D",
                str(data),
                "-U",
                "postgres",
                "-A",
                "scram-sha-256",
                "--locale=C",
                "-E",
                "UTF8",
                "--pwfile=" + str(native().KEYS / "secrets/postgres_superuser_password"),
            ]
        )
    spawn(
        [
            "postgres",
            "-D",
            str(data),
            "-p",
            "55432",
            "-h",
            "127.0.0.1",
            "-c",
            "unix_socket_directories=/tmp",
            "-c",
            "shared_buffers=16MB",
            "-c",
            "max_connections=32",
            "-c",
            "work_mem=1MB",
            "-c",
            "max_parallel_workers=0",
        ]
    )
    for _ in range(60):
        if native().postgres_running():
            return
        if children[0].poll() is not None:
            raise SystemExit("PostgreSQL failed during startup")
        time.sleep(1)
    raise SystemExit("PostgreSQL startup timed out")


def stop_children(children: list[subprocess.Popen[bytes]]) -> None:
    # Stop web/worker first and PostgreSQL last, leaving time for a clean DB shutdown.
    for child in reversed(children):
        if child.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=8)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()


def start_nginx(spawn: Callable[..., None]) -> None:
    (STATE / "ready").unlink(missing_ok=True)
    config = (REPO / "ops/render/nginx.conf").read_text()
    nginx = STATE / "nginx.conf"
    nginx.write_text(config.replace("__PORT__", os.environ.get("PORT", "10000")))
    spawn(["nginx", "-c", str(nginx), "-g", "daemon off;"])


def start() -> None:
    settings()
    if os.environ.get("SAFEPAY_MODE", "demo") != "demo" or os.environ.get("SAFEPAY_LLM", "mock") != "mock":
        raise SystemExit("This disposable deployment supports only demo mode and mock LLM")
    STATE.mkdir(parents=True, exist_ok=True)
    run([sys.executable, str(REPO / "ops/scripts/gen_dev_keys.py"), str(native().KEYS)])
    children: list[subprocess.Popen[bytes]] = []

    def stop(_signal: int, _frame: Any) -> None:
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def spawn(command: list[str], **kwargs: Any) -> None:
        children.append(subprocess.Popen(command, start_new_session=True, **kwargs))  # noqa: S603

    def healthy(port: int) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
                return response.status == 200
        except OSError:
            return False

    try:
        start_postgres(spawn, children)
        # Bind the public port before slow migrations; readiness stays HTTP 503.
        start_nginx(spawn)
        native().create_databases()
        native().migrate()
        run([sys.executable, __file__, "seed"])
        run([sys.executable, __file__, "login"])
        public_host = os.environ.get("RENDER_EXTERNAL_HOSTNAME", "localhost")
        allowed_hosts = ",".join(["localhost", "127.0.0.1", public_host])
        for service, directory in (("bank", "bank_sandbox"), ("intel", "intel"), ("core", "core")):
            env = native().service_env(service)
            env.update(
                DJANGO_ALLOWED_HOSTS=allowed_hosts, DJANGO_CSRF_TRUSTED_ORIGINS=f"https://{public_host}"
            )
            spawn(
                [
                    "gunicorn",
                    f"{native().PACKAGES[service]}.wsgi:application",
                    "--bind",
                    f"127.0.0.1:{native().HTTP_PORTS[service]}",
                    "--workers",
                    "1",
                    "--preload",
                    "--no-control-socket",
                    "--access-logfile",
                    "-",
                ],
                cwd=REPO / directory,
                env=env,
            )
        spawn(
            [sys.executable, "manage.py", "outbox_worker", "--interval", "2"],
            cwd=REPO / "core",
            env=native().service_env("core"),
        )
        for _ in range(90):
            if all(healthy(port) for port in native().HTTP_PORTS.values()):
                break
            if any(child.poll() is not None for child in children):
                raise SystemExit("A demo service exited during startup")
            time.sleep(1)
        else:
            raise SystemExit("Django services did not become healthy")
        (STATE / "ready").touch()
        print("SafePay disposable demo ready. Data resets when Render replaces the instance.")
        while True:
            if any(child.poll() is not None for child in children):
                raise SystemExit("A demo process exited; stopping the instance for a clean restart")
            time.sleep(1)
    finally:
        stop_children(children)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="start", choices=("start", "seed", "login"))
    {"start": start, "seed": seed, "login": login}[parser.parse_args().command]()
