"""Run a service's Django management command natively (local PostgreSQL) or inside docker compose.

native  the shared uv environment and ops/dev-keys on this machine; services reach each other on
        127.0.0.1:8000/8001/8002 (see ops/scripts/native_stack.py).
docker  `docker compose run` on the stack from ops/docker-compose.yml, with the repository mounted at
        /repo so plans, twins and evidence written here are visible inside the container.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

SERVICES = {
    "core": ("core", "safepay_core", "core-web", "core"),
    "intel": ("intel", "safepay_intel", "intel", "intel"),
    "bank": ("bank_sandbox", "safepay_bank", "bank", "bank"),
}


@dataclass(frozen=True)
class Runner:
    mode: str  # native | docker
    keys_dir: Path = REPO / "ops" / "dev-keys"

    @classmethod
    def detect(cls) -> Runner:
        mode = os.environ.get("SAFEPAY_RUNNER") or ("docker" if shutil.which("docker") else "native")
        if mode not in ("native", "docker"):
            raise SystemExit(f"SAFEPAY_RUNNER must be native or docker, not {mode!r}")
        return cls(mode)

    # -------------------------------------------------------------------------------- paths
    def path(self, host: Path) -> str:
        """A host path as the command will see it."""
        host = host.resolve()
        if self.mode == "native":
            return str(host)
        return "/repo/" + host.relative_to(REPO).as_posix()

    # -------------------------------------------------------------------------------- commands
    def manage(
        self,
        service: str,
        args: list[str],
        *,
        settings: str = "prod",
        owner: bool = False,
        live: bool = False,
        extra_env: dict[str, str] | None = None,
    ) -> str:
        """Run manage.py and return its stdout.

        live=True marks commands that use the running stack's own state (tenant keystore, member keys and
        the PII key in the core volume). In docker they run as the image's service user, which owns that
        volume; other commands run as the host user so the files they write here stay the user's.
        """
        directory, package, compose_service, svc = SERVICES[service]
        role = "owner" if owner else "app"
        env = {
            "DJANGO_SETTINGS_MODULE": f"{package}.settings.{settings}",
            "DB_NAME": f"safepay_{svc}",
            "DB_USER": f"safepay_{svc}_{role}",
            "SAFEPAY_LLM": "mock",
            **(extra_env or {}),
        }
        if self.mode == "native":
            secrets = self.keys_dir / "secrets"
            env |= {
                "DB_HOST": os.environ.get("DB_HOST", "127.0.0.1"),
                "DB_PORT": os.environ.get("SAFEPAY_PG_PORT", "55432"),
                "DB_PASSWORD_FILE": str(secrets / f"{svc}_{role}_db_password"),
                "DJANGO_SECRET_KEY_FILE": str(secrets / f"{svc}_django_secret_key"),
                "SAFEPAY_KEYS_DIR": str(self.keys_dir),
                "PYTHONUNBUFFERED": "1",
            }
            if service == "core":
                env.setdefault("SAFEPAY_INTEL_URL", "http://127.0.0.1:8001")
                env.setdefault("SAFEPAY_BANK_URL", "http://127.0.0.1:8002")
                runtime = Path.home() / ".safepay-dev" / "coredata"
                env.setdefault("SAFEPAY_KEYSTORE_DIR", str(runtime / "tenant-keys"))
                env.setdefault("SAFEPAY_MEMBER_KEYS_DIR", str(runtime / "members"))
            command = [sys.executable, "manage.py", *args]
            completed = subprocess.run(  # noqa: S603 (fixed argv, no shell)
                command,
                cwd=REPO / directory,
                env={**os.environ, **env},
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            secrets_in = "/run/secrets/safepay/secrets"
            env |= {
                "DB_PASSWORD_FILE": f"{secrets_in}/{svc}_{role}_db_password",
                "DJANGO_SECRET_KEY_FILE": f"{secrets_in}/{svc}_django_secret_key",
            }
            command = [
                "docker",
                "compose",
                "-f",
                str(REPO / "ops" / "docker-compose.yml"),
                "run",
                "--rm",
                "-T",
                "--no-deps",
            ]
            # Non-live commands write into the bind-mounted repository, so they run as its owner (root
            # included: a root-owned checkout is not writable by the image user 10001).
            if not live:
                uid = os.environ.get("SAFEPAY_UID") or str(os.getuid() if hasattr(os, "getuid") else 0)
                gid = os.environ.get("SAFEPAY_GID") or str(os.getgid() if hasattr(os, "getgid") else 0)
                command += ["--user", f"{uid}:{gid}"]
            command += ["-v", f"{REPO}:/repo", "-w", f"/app/{directory}"]
            for key, value in env.items():
                command += ["-e", f"{key}={value}"]
            for key in ("ANTHROPIC_API_KEY", "SAFEPAY_LLM_MODEL"):  # inherited by name, never on argv
                if os.environ.get(key):
                    command += ["-e", key]
            command += [compose_service, "python", "manage.py", *args]
            completed = subprocess.run(command, capture_output=True, text=True, check=False)  # noqa: S603
        if completed.returncode != 0:
            tail = "\n".join((completed.stdout + completed.stderr).splitlines()[-25:])
            raise SystemExit(f"{service} manage.py {' '.join(args[:1])} failed:\n{tail}")
        return completed.stdout
