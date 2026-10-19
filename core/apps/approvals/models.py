"""Four-eyes records. The database independently forbids self-approval and duplicate approvals."""

from __future__ import annotations

import uuid

from django.db import models


class Approval(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    payment_id = models.UUIDField()
    approver_id = models.UUIDField()
    initiator_id = models.UUIDField()
    action = models.CharField(max_length=8)
    reason = models.CharField(max_length=500)
    reject_reason = models.CharField(max_length=16, blank=True)
    acknowledged_warnings = models.JSONField(default=list)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "approvals_approval"
        constraints = [  # noqa: RUF012
            models.CheckConstraint(
                condition=~models.Q(approver_id=models.F("initiator_id")), name="approvals_no_self_approval"
            ),
            models.UniqueConstraint(
                fields=["tenant_id", "payment_id", "approver_id"],
                condition=models.Q(action="APPROVE"),
                name="approvals_distinct_user",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.action} {self.payment_id} by {self.approver_id}"
