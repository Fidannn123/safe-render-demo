"""Epochs: epoch n covers [n * oprf_epoch_days, (n + 1) * oprf_epoch_days) days since the Unix epoch (UTC)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from django.db import IntegrityError, transaction

from apps.oprf.models import EpochKey
from apps.oprf.port import SEED_BYTES, OprfServer
from safepay_intel.policy import network_policy

_UNIX = datetime(1970, 1, 1, tzinfo=UTC)
KEY_INFO_PREFIX = b"safepay/v1/oprf-epoch/"


def epoch_length() -> timedelta:
    return timedelta(days=network_policy().oprf_epoch_days)


def epoch_number(now: datetime) -> int:
    return (now - _UNIX) // epoch_length()


def epoch_bounds(epoch: int) -> tuple[datetime, datetime]:
    start = _UNIX + epoch * epoch_length()
    return start, start + epoch_length()


@dataclass(frozen=True)
class Epoch:
    epoch: int
    public_key: bytes
    starts_at: datetime
    ends_at: datetime


def key_for(epoch: int, *, create: bool) -> EpochKey | None:
    key = EpochKey.objects.filter(epoch=epoch).first()
    if key is not None or not create:
        return key
    seed = secrets.token_bytes(SEED_BYTES)
    key_info = KEY_INFO_PREFIX + str(epoch).encode("ascii")
    public_key = OprfServer(seed, key_info).public_key
    try:
        with transaction.atomic():
            return EpochKey.objects.create(epoch=epoch, seed=seed, key_info=key_info, public_key=public_key)
    except IntegrityError:  # created concurrently
        return EpochKey.objects.get(epoch=epoch)


def current_epoch(now: datetime) -> Epoch:
    n = epoch_number(now)
    key = key_for(n, create=True)
    if key is None:  # pragma: no cover (create=True always returns a key)
        raise RuntimeError("epoch key missing")
    starts_at, ends_at = epoch_bounds(n)
    return Epoch(epoch=n, public_key=bytes(key.public_key), starts_at=starts_at, ends_at=ends_at)


def server_for(key: EpochKey) -> OprfServer:
    return OprfServer(bytes(key.seed), bytes(key.key_info))
