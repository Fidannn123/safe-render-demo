"""guardian_decision: append-only record of every signed Decision (DECISIONS.md D-033).

The app role has SELECT/INSERT only and a trigger blocks UPDATE/DELETE for every role (db_policy.py);
the tenant_id column puts the table under FORCE RLS. nonce is UNIQUE (single-use Decisions).
inputs_canonical holds exactly the RFC 8785 bytes (UTF-8 text) that inputs_sha256 hashes, so a past
verdict can be replayed and approval can detect PAYEE_CHANGED (D-029).
"""

from __future__ import annotations

from django.db import models


class GuardianDecision(models.Model):
    decision_id = models.UUIDField(primary_key=True)
    tenant_id = models.UUIDField()
    payment_id = models.UUIDField()
    verdict = models.CharField(max_length=8)
    tier = models.CharField(max_length=2, null=True)  # noqa: DJ001  (Decision.tier is null for DENY)
    score = models.SmallIntegerField()
    reasons = models.JSONField()
    policy_version = models.CharField(max_length=32)
    network_epoch = models.BigIntegerField(null=True)
    inputs_sha256 = models.CharField(max_length=64)
    inputs_canonical = models.TextField()
    nonce = models.CharField(max_length=64, unique=True)
    issued_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    key_id = models.CharField(max_length=128)
    sig = models.CharField(max_length=88)
    signed_payload = models.TextField()  # RFC 8785 JSON of the Decision without sig (the signed bytes)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "guardian_decision"
        indexes = [  # noqa: RUF012
            models.Index(fields=["tenant_id", "payment_id"], name="guardian_dec_tenant_pay_idx"),
        ]
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                condition=models.Q(verdict__in=["ALLOW", "GUARDED", "HOLD", "DENY"]),
                name="guardian_decision_verdict_valid",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(verdict="ALLOW", tier="T1")
                    | models.Q(verdict="GUARDED", tier="T2")
                    | models.Q(verdict="HOLD", tier="T3")
                    | models.Q(verdict="DENY", tier__isnull=True)
                ),
                name="guardian_decision_tier_matches_verdict",
            ),
            models.CheckConstraint(
                condition=models.Q(score__gte=0, score__lte=100), name="guardian_decision_score_range"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.decision_id} {self.verdict}"
