"""Signed daily statements (spec/schemas/Statement.json). APPEND-ONLY (db_policy trigger)."""

from __future__ import annotations

from django.db import models

from apps.accounts.models import Account


class Statement(models.Model):
    """A statement as issued. A closed day (date < today UTC) is issued once (final) and re-served;
    a statement for the current day is a signed interim snapshot, issued anew on each request."""

    statement_id = models.UUIDField(primary_key=True)
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="statements")
    date = models.DateField()
    final = models.BooleanField()
    document = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "statements_statement"
        constraints = [  # noqa: RUF012 (Django Meta)
            models.UniqueConstraint(
                fields=["account", "date"], condition=models.Q(final=True), name="statement_one_final_per_day"
            )
        ]

    def __str__(self) -> str:
        return f"{self.account_id} {self.date}"
