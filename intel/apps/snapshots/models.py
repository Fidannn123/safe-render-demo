"""Signed per-epoch tag snapshots. APPEND-ONLY (db_policy, D-033): a change publishes a new seq."""

from __future__ import annotations

from django.db import models


class Snapshot(models.Model):
    epoch = models.IntegerField()
    seq = models.IntegerField()
    issued_at = models.DateTimeField()
    valid_until = models.DateTimeField()
    entries_sha256 = models.CharField(max_length=64)
    signed_snapshot = models.TextField()  # RFC 8785 canonical JSON of the full Snapshot incl. sig

    class Meta:
        db_table = "snapshots_snapshot"
        constraints = (models.UniqueConstraint(fields=["epoch", "seq"], name="snapshots_epoch_seq"),)

    def __str__(self) -> str:
        return f"snapshot {self.epoch}/{self.seq}"
