"""Build, sign and serve per-epoch tag snapshots (spec/schemas/Snapshot.json).

tag = first tag_bytes bytes of RFC 9497 Evaluate(k_e, x) with x = payee_input(country, IBAN): exactly
the Finalize output a member computes through blind evaluation, so lookups match without revealing x.
pk_e (oprf_public_key) is pinned through the Ed25519 snapshot signature.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Max

from apps.oprf.epochs import epoch_number, key_for, server_for
from apps.oprf.models import EpochKey
from apps.oprf.payee import payee_input, tag_of
from apps.oprf.port import MODE, SUITE
from apps.reports.models import Report
from apps.snapshots.models import Snapshot
from safepay_intel.crypto import b64encode, canonical_json, load_private_key_pem_file, sha256_hex, sign_object
from safepay_intel.policy import network_policy

MIN_REMAINING_VALIDITY = timedelta(seconds=60)


def rfc3339(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_entries(key: EpochKey, now: datetime) -> list[dict[str, Any]]:
    """Active reports grouped by payee: distinct reporters and the latest expiry. Sorted by tag."""
    rows = (
        Report.objects.filter(retracted_at__isnull=True, expires_at__gt=now)
        .values("payee_country", "payee_iban")
        .annotate(reporters=Count("member", distinct=True), expires=Max("expires_at"))
    )
    server = server_for(key)
    entries = [
        {
            "tag": tag_of(server.evaluate(payee_input(r["payee_country"], r["payee_iban"]))),
            "distinct_reporters": int(r["reporters"]),
            "expires_at": rfc3339(r["expires"]),
        }
        for r in rows
    ]
    entries.sort(key=lambda e: str(e["tag"]))
    return entries


def _publish(key: EpochKey, seq: int, entries: list[dict[str, Any]], now: datetime) -> Snapshot:
    valid_until = now + timedelta(seconds=network_policy().snapshot_pull_seconds)
    body: dict[str, Any] = {
        "epoch": key.epoch,
        "seq": seq,
        "issued_at": rfc3339(now),
        "valid_until": rfc3339(valid_until),
        "suite": SUITE,
        "mode": MODE,
        "oprf_public_key": b64encode(bytes(key.public_key)),
        "tag_bytes": network_policy().tag_bytes,
        "entries": entries,
        "key_id": settings.SAFEPAY_INTEL_SNAPSHOT_KEY_ID,
    }
    body["sig"] = sign_object(load_private_key_pem_file(settings.SAFEPAY_INTEL_SNAPSHOT_KEY_FILE), body)
    return Snapshot.objects.create(
        epoch=key.epoch,
        seq=seq,
        issued_at=now,
        valid_until=valid_until,
        entries_sha256=sha256_hex(canonical_json(entries)),
        signed_snapshot=canonical_json(body).decode("utf-8"),
    )


def snapshot_for(epoch: int, now: datetime) -> dict[str, Any] | None:
    """Latest snapshot of `epoch`. For the current epoch, publish a new seq when stale or changed."""
    if epoch != epoch_number(now):
        latest = Snapshot.objects.filter(epoch=epoch).order_by("-seq").first()
        return None if latest is None else _load(latest)
    with transaction.atomic():
        key_for(epoch, create=True)
        key = EpochKey.objects.select_for_update().get(epoch=epoch)  # serialises seq allocation
        latest = Snapshot.objects.filter(epoch=epoch).order_by("-seq").first()
        entries = current_entries(key, now)
        # Never hand out a snapshot about to expire: a client checking it a moment later would reject it.
        fresh = latest is not None and latest.valid_until > now + MIN_REMAINING_VALIDITY
        same = latest is not None and latest.entries_sha256 == sha256_hex(canonical_json(entries))
        if latest is not None and fresh and same:
            return _load(latest)
        seq = 0 if latest is None else latest.seq + 1
        return _load(_publish(key, seq, entries, now))


def _load(snapshot: Snapshot) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(snapshot.signed_snapshot)
    return data
