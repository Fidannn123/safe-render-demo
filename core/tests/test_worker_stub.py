from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command


@pytest.mark.django_db
def test_outbox_worker_single_tick_writes_heartbeat(tmp_path: Path) -> None:
    out = StringIO()
    heartbeat = tmp_path / "hb"
    call_command("outbox_worker", "--once", f"--heartbeat={heartbeat}", stdout=out)
    assert "delivery started" in out.getvalue()
    assert "'processed': 0" in out.getvalue()
    assert float(heartbeat.read_text(encoding="utf-8")) > 0
