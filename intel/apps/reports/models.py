"""Accountable reports. Report rows carry mutable retraction state; receipts are append-only evidence."""

from __future__ import annotations

from django.db import models

from apps.members.models import Member


class Report(models.Model):
    class Category(models.TextChoices):
        INVOICE_REDIRECTION = "INVOICE_REDIRECTION"
        FAKE_VENDOR = "FAKE_VENDOR"
        IMPERSONATION = "IMPERSONATION"
        OTHER_FRAUD = "OTHER_FRAUD"

    report_id = models.UUIDField(primary_key=True)
    member = models.ForeignKey(Member, on_delete=models.PROTECT, related_name="reports")
    payee_country = models.CharField(max_length=2)
    payee_iban = models.CharField(max_length=34)
    category = models.CharField(max_length=32, choices=Category.choices)
    evidence_sha256 = models.CharField(max_length=64)
    key_id = models.CharField(max_length=128)
    request_sha256 = models.CharField(max_length=64)  # canonical JSON of the whole signed report
    signed_report = models.TextField()  # RFC 8785 canonical JSON incl. sig (accountability record)
    admitted_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    retracted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "reports_report"
        indexes = (models.Index(fields=["payee_country", "payee_iban"], name="reports_payee_idx"),)

    def __str__(self) -> str:
        return str(self.report_id)


class Receipt(models.Model):
    """Bank-signed settlement receipt that backed an admitted report. APPEND-ONLY (db_policy, D-033)."""

    report = models.OneToOneField(Report, on_delete=models.PROTECT, related_name="receipt")
    receipt_id = models.UUIDField()
    key_id = models.CharField(max_length=128)
    booked_at = models.DateTimeField()
    signed_receipt = models.TextField()  # RFC 8785 canonical JSON incl. sig
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "reports_receipt"

    def __str__(self) -> str:
        return str(self.receipt_id)
