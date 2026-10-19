"""Transactional outbox worker, periodic reconciliation and compose liveness heartbeat."""

from __future__ import annotations

import os
import signal
import tempfile
import time
from pathlib import Path
from types import FrameType
from typing import Any

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection

from apps.bank_adapter.service import process_outbox
from apps.recon.service import reconcile_tenant

DEFAULT_HEARTBEAT = os.environ.get(
    "SAFEPAY_WORKER_HEARTBEAT", str(Path(tempfile.gettempdir()) / "safepay-worker.heartbeat")
)


class Command(BaseCommand):
    help = "Deliver transactional outbox rows and reconcile signed bank statements every five minutes."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--interval", type=float, default=5.0)
        parser.add_argument("--once", action="store_true", help="Run a single tick and exit.")
        parser.add_argument("--batch-size", type=int, default=25)
        parser.add_argument(
            "--heartbeat", default=DEFAULT_HEARTBEAT, help="Heartbeat file for the healthcheck."
        )

    def handle(self, *args: Any, **options: Any) -> None:
        stop = False
        heartbeat = Path(options["heartbeat"])

        def _stop(signum: int, frame: FrameType | None) -> None:
            nonlocal stop
            stop = True

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        self.stdout.write("outbox_worker: delivery started")
        next_recon = 0.0
        while not stop:
            try:
                connection.ensure_connection()
                stats = process_outbox(batch_size=options["batch_size"])
                self.stdout.write(f"outbox_worker: {stats}")
                if time.monotonic() >= next_recon:
                    from apps.tenants.models import Tenant  # noqa: PLC0415

                    for tenant in Tenant.objects.all():
                        try:
                            reconcile_tenant(tenant.id)
                        except Exception as exc:
                            self.stderr.write(f"reconciliation deferred: {type(exc).__name__}")
                    next_recon = time.monotonic() + 300
                from apps.audit.checkpoints import checkpoint  # noqa: PLC0415
                from apps.tenants.models import Tenant  # noqa: PLC0415

                for tenant_id in Tenant.objects.values_list("id", flat=True):
                    checkpoint(tenant_id)
                heartbeat.write_text(str(time.time()), encoding="utf-8")
            except DatabaseError as exc:
                self.stderr.write(f"outbox_worker: database unavailable: {exc}")
            if options["once"]:
                break
            time.sleep(min(60, max(0.1, options["interval"])))
        self.stdout.write("outbox_worker: stopped")
