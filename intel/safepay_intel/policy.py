"""Network policy values from spec/policy.yaml (section `network`). Never hard-coded elsewhere."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

import yaml
from django.conf import settings


@dataclass(frozen=True)
class NetworkPolicy:
    oprf_quota_per_member_per_day: int
    oprf_epoch_days: int
    snapshot_pull_seconds: int
    report_expiry_days: int
    receipt_max_age_days: int
    tag_bytes: int
    input_domain: str


@cache
def network_policy() -> NetworkPolicy:
    data = yaml.safe_load((settings.SAFEPAY_SPEC_DIR / "policy.yaml").read_text(encoding="utf-8"))
    net = data["network"]
    return NetworkPolicy(
        oprf_quota_per_member_per_day=int(net["oprf_quota_per_member_per_day"]),
        oprf_epoch_days=int(net["oprf_epoch_days"]),
        snapshot_pull_seconds=int(net["snapshot_pull_seconds"]),
        report_expiry_days=int(net["report_expiry_days"]),
        receipt_max_age_days=int(net["receipt_max_age_days"]),
        tag_bytes=int(net["tag_bytes"]),
        input_domain=str(net["input_domain"]),
    )
