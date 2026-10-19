"""Run the SafePay stack WITHOUT Docker (Windows, macOS, Linux). DEV/DEMO ONLY; Docker remains the default.

Needs: uv (repo synced), pnpm, and PostgreSQL 18 server binaries (set PG_BIN to their directory, e.g. an
unzipped EDB "binaries" archive). State lives in SAFEPAY_NATIVE_HOME (default ~/.safepay-dev).

  uv run python ops/scripts/native_stack.py init     # cluster on 127.0.0.1:55432, roles, databases, migrate
  uv run python ops/scripts/native_stack.py start    # postgres, bank :8002, intel :8001, core :8000, worker,
                                                     # console :5173 (Vite dev server, proxies /api to core)
  uv run python ops/scripts/native_stack.py status
  uv run python ops/scripts/native_stack.py stop
  uv run python ops/scripts/native_stack.py reset    # drop and recreate the three databases (wipes data)

Then: SAFEPAY_RUNNER=native make seed / demo / evidence (or run python -m safepay_sim.harness from sim/).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEYS = Path(os.environ.get("SAFEPAY_KEYS_DIR", REPO / "ops" / "dev-keys"))
HOME = Path(os.environ.get("SAFEPAY_NATIVE_HOME", Path.home() / ".safepay-dev"))
PGDATA, LOGS, PIDS = HOME / "pgdata", HOME / "logs", HOME / "pids.json"
PORT = os.environ.get("SAFEPAY_PG_PORT", "55432")
SERVICES = (("core", "core"), ("intel", "intel"), ("bank", "bank_sandbox"))
PACKAGES = {"core": "safepay_core", "intel": "safepay_intel", "bank": "safepay_bank"}
HTTP_PORTS = {"bank": 8002, "intel": 8001, "core": 8000}
WINDOWS = os.name == "nt"


def pg(tool: str) -> str:
    base = os.environ.get("PG_BIN")
    path = shutil.which(tool, path=base) if base else shutil.which(tool)
    if not path:
        raise SystemExit(f"{tool} not found: set PG_BIN to the PostgreSQL 18 bin directory")
    return path


def secret(name: str) -> str:
    return (KEYS / "secrets" / name).read_text(encoding="utf-8").strip()


def psql(sql: str, database: str = "postgres") -> None:
    env = {**os.environ, "PGPASSWORD": secret("postgres_superuser_password")}
    command = [
        pg("psql"),
        "-v",
        "ON_ERROR_STOP=1",
        "-q",
        "-h",
        "127.0.0.1",
        "-p",
        PORT,
        "-U",
        "postgres",
        "-d",
        database,
    ]
    subprocess.run(command, input=sql, text=True, env=env, check=True)


def service_env(svc: str, role: str = "app") -> dict[str, str]:
    env = {
        **os.environ,
        "DJANGO_SETTINGS_MODULE": f"{PACKAGES[svc]}.settings.prod",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": PORT,
        "DB_NAME": f"safepay_{svc}",
        "DB_USER": f"safepay_{svc}_{role}",
        "DB_APP_ROLE": f"safepay_{svc}_app",
        "DB_PASSWORD_FILE": str(KEYS / "secrets" / f"{svc}_{role}_db_password"),
        "DJANGO_SECRET_KEY_FILE": str(KEYS / "secrets" / f"{svc}_django_secret_key"),
        "DJANGO_ALLOWED_HOSTS": "localhost,127.0.0.1",
        "SAFEPAY_KEYS_DIR": str(KEYS),
        "PYTHONUNBUFFERED": "1",
    }
    if svc == "core":
        env |= {
            "SAFEPAY_MODE": os.environ.get("SAFEPAY_MODE", "demo"),
            "SAFEPAY_LLM": os.environ.get("SAFEPAY_LLM", "mock"),
            "SAFEPAY_INTEL_URL": "http://127.0.0.1:8001",
            "SAFEPAY_BANK_URL": "http://127.0.0.1:8002",
            "SAFEPAY_KEYSTORE_DIR": str(HOME / "coredata" / "tenant-keys"),
            "SAFEPAY_MEMBER_KEYS_DIR": str(HOME / "coredata" / "members"),
            "DJANGO_CSRF_TRUSTED_ORIGINS": "http://localhost:5173,http://127.0.0.1:5173",
        }
    return env


def create_databases() -> None:
    for svc in ("core", "intel", "bank"):
        owner, app = secret(f"{svc}_owner_db_password"), secret(f"{svc}_app_db_password")
        psql(
            f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'safepay_{svc}_owner') THEN
    CREATE ROLE safepay_{svc}_owner LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS
      PASSWORD '{owner}';
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'safepay_{svc}_app') THEN
    CREATE ROLE safepay_{svc}_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS
      NOINHERIT PASSWORD '{app}';
  END IF;
END $$;
SELECT 'CREATE DATABASE safepay_{svc} OWNER safepay_{svc}_owner'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'safepay_{svc}')\\gexec
REVOKE ALL ON DATABASE safepay_{svc} FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE safepay_{svc} TO safepay_{svc}_owner;
GRANT CONNECT ON DATABASE safepay_{svc} TO safepay_{svc}_app;
"""
        )
        psql(
            f"REVOKE ALL ON SCHEMA public FROM PUBLIC; ALTER SCHEMA public OWNER TO safepay_{svc}_owner;"
            f" GRANT USAGE ON SCHEMA public TO safepay_{svc}_app;",
            f"safepay_{svc}",
        )
    psql(
        "REVOKE CONNECT ON DATABASE postgres FROM PUBLIC; GRANT CONNECT ON DATABASE postgres TO "
        "safepay_core_owner, safepay_intel_owner, safepay_bank_owner;"
    )


def migrate() -> None:
    for svc, directory in SERVICES:
        print(f"migrate {svc}")
        subprocess.run(
            [sys.executable, "manage.py", "migrate", "--noinput", "-v", "0"],
            cwd=REPO / directory,
            env=service_env(svc, "owner"),
            check=True,
        )


def postgres_running() -> bool:
    return (
        subprocess.run([pg("pg_isready"), "-q", "-h", "127.0.0.1", "-p", PORT], check=False).returncode == 0
    )


def start_postgres() -> None:
    if postgres_running():
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    options = f"-p {PORT} -c listen_addresses=127.0.0.1 -c max_connections=200"
    # Null std handles: on Windows pg_ctl keeps a cmd.exe wrapper alive for the server's lifetime, and
    # an inherited pipe (`make native-up | tail`) would then never reach end-of-file.
    subprocess.run(
        [pg("pg_ctl"), "-D", str(PGDATA), "-o", options, "-l", str(LOGS / "postgres.log"), "-w", "start"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )


def cmd_init(_: argparse.Namespace) -> None:
    subprocess.run([sys.executable, str(REPO / "ops" / "scripts" / "gen_dev_keys.py"), str(KEYS)], check=True)
    if not (PGDATA / "PG_VERSION").exists():
        HOME.mkdir(parents=True, exist_ok=True)
        pwfile = HOME / "pwfile"
        pwfile.write_text(secret("postgres_superuser_password"), encoding="utf-8")
        try:
            subprocess.run(
                [
                    pg("initdb"),
                    "-D",
                    str(PGDATA),
                    "-U",
                    "postgres",
                    f"--pwfile={pwfile}",
                    "-A",
                    "scram-sha-256",
                    "-E",
                    "UTF8",
                    "--locale=C",
                ],
                check=True,
            )
        finally:
            pwfile.unlink()
    start_postgres()
    create_databases()
    migrate()
    print("native stack initialised; next: start")


def spawn(name: str, command: list[str], cwd: Path, env: dict[str, str]) -> int:
    LOGS.mkdir(parents=True, exist_ok=True)
    log = (LOGS / f"{name}.log").open("ab")
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if WINDOWS else 0  # type: ignore[attr-defined,unused-ignore]
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        start_new_session=not WINDOWS,
    )
    return process.pid


def healthy(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2) as response:
            return bool(response.status == 200)
    except OSError:
        return False


def cmd_start(_: argparse.Namespace) -> None:
    start_postgres()
    pids: dict[str, int] = json.loads(PIDS.read_text()) if PIDS.exists() else {}
    for svc, directory in (("bank", "bank_sandbox"), ("intel", "intel"), ("core", "core")):
        if healthy(HTTP_PORTS[svc]):
            continue
        command = [sys.executable, "manage.py", "runserver", "--noreload", f"127.0.0.1:{HTTP_PORTS[svc]}"]
        pids[svc] = spawn(svc, command, REPO / directory, service_env(svc))
    for _ in range(60):
        if all(healthy(port) for port in HTTP_PORTS.values()):
            break
        time.sleep(1)
    if "worker" not in pids:
        command = [sys.executable, "manage.py", "outbox_worker", "--interval", "2"]
        pids["worker"] = spawn("worker", command, REPO / "core", service_env("core"))
    if "console" not in pids:
        pnpm = shutil.which("pnpm") or "pnpm"
        pids["console"] = spawn(
            "console",
            [pnpm, "--filter", "@safepay/console", "dev", "--host", "127.0.0.1"],
            REPO,
            dict(os.environ),
        )
    PIDS.write_text(json.dumps(pids), encoding="utf-8")
    cmd_status(_)
    print("console: http://127.0.0.1:5173/console  (landing page at /, logs in", LOGS, ")")


def stop_pid(pid: int) -> None:
    if WINDOWS:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True, check=False)
    else:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGTERM)


def cmd_stop(_: argparse.Namespace) -> None:
    if PIDS.exists():
        for name, pid in json.loads(PIDS.read_text()).items():
            stop_pid(pid)
            print(f"stopped {name}")
        PIDS.unlink()
    if postgres_running():
        subprocess.run([pg("pg_ctl"), "-D", str(PGDATA), "-m", "fast", "-w", "stop"], check=False)


def cmd_status(_: argparse.Namespace) -> None:
    print(f"postgres :{PORT} {'up' if postgres_running() else 'down'}")
    for svc, port in HTTP_PORTS.items():
        print(f"{svc:<7} :{port} {'healthy' if healthy(port) else 'down'}")


def cmd_reset(_: argparse.Namespace) -> None:
    start_postgres()
    for svc in ("core", "intel", "bank"):
        psql(f"DROP DATABASE IF EXISTS safepay_{svc} WITH (FORCE);")
    create_databases()
    migrate()
    for path in (HOME / "coredata", REPO / "ops" / "dev-keys" / "demo"):
        shutil.rmtree(path, ignore_errors=True)
    print("databases recreated; run seed again")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("init", "start", "stop", "status", "reset"))
    options = parser.parse_args()
    {"init": cmd_init, "start": cmd_start, "stop": cmd_stop, "status": cmd_status, "reset": cmd_reset}[
        options.command
    ](options)


if __name__ == "__main__":
    main()
