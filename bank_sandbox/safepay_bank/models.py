"""Project-level models (request-signing nonce store). Also makes Django emit post_migrate (db_policy)."""

from __future__ import annotations

from django.db import models


class RequestNonce(models.Model):
    """Single-use request nonces (D-012). Rows older than the skew window are pruned (DELETE allowed)."""

    nonce = models.CharField(max_length=64, primary_key=True)
    key_id = models.CharField(max_length=128)
    request_ts = models.BigIntegerField(db_index=True)
    seen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "safepay_bank_requestnonce"

    def __str__(self) -> str:
        return self.nonce
