"""Epoch OPRF keys and per-member evaluation quota counters."""

from __future__ import annotations

from django.db import models

from apps.members.models import Member


class EpochKey(models.Model):
    """One OPRF key per epoch (oprf_epoch_days). The secret is the RFC 9497 DeriveKeyPair seed.

    Dev/demo: the seed lives in the intel database (only the intel roles can connect). Production: HSM /
    KMS through the same OprfServer port (docs/architecture.md key table).
    """

    epoch = models.IntegerField(primary_key=True)
    seed = models.BinaryField(max_length=32)
    key_info = models.BinaryField(max_length=64)
    public_key = models.BinaryField(max_length=32)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "oprf_epoch_key"

    def __str__(self) -> str:
        return f"epoch {self.epoch}"


class QuotaUsage(models.Model):
    """Evaluations per member per UTC day (one per blinded element)."""

    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="+")
    day = models.DateField()
    count = models.IntegerField(default=0)

    class Meta:
        db_table = "oprf_quota_usage"
        constraints = (
            models.UniqueConstraint(fields=["member", "day"], name="oprf_quota_member_day"),
            models.CheckConstraint(condition=models.Q(count__gte=0), name="oprf_quota_nonneg"),
        )

    def __str__(self) -> str:
        return f"{self.member_id} {self.day}: {self.count}"
