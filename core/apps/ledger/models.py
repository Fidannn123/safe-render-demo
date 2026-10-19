"""Double-entry ledger (architecture.md section 2.4, spec/ledger_accounts.yaml).

Amounts are BIGINT minor units with an ISO-4217 currency; a posting's amount is signed (debit > 0,
credit < 0). PostgreSQL enforces, independently of this code (migration 0001):
  * a DEFERRABLE INITIALLY DEFERRED constraint trigger: every journal entry has >= 2 postings and its
    postings sum to zero per currency at COMMIT;
  * ledger_balance rows are maintained ONLY by the AFTER INSERT trigger on ledger_posting (direct
    INSERT/UPDATE/DELETE of a balance row raises), and CHECK (NOT non_negative OR balance >= 0)
    makes Bank:available / Bank:reserved / Bank:in_transit overdraft impossible;
  * ledger_journal_entry and ledger_posting are append-only (db_policy.py), ledger_account is immutable.
"""

from __future__ import annotations

import uuid

from django.db import models


class Account(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    code = models.CharField(max_length=80)  # "Bank:available", "AP:<vendor uuid>", ...
    spec_key = models.CharField(max_length=40)  # key in ledger_accounts.yaml, e.g. "AP:<vendor>"
    type = models.CharField(max_length=16)
    normal_sign = models.SmallIntegerField()  # +1 debit-normal, -1 credit-normal
    non_negative = models.BooleanField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_account"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["tenant_id", "code"], name="ledger_account_tenant_code_uniq"),
            models.CheckConstraint(
                condition=models.Q(normal_sign__in=[1, -1]), name="ledger_account_normal_sign_chk"
            ),
        ]

    def __str__(self) -> str:
        return self.code


class JournalEntry(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    template = models.CharField(max_length=64)
    payment_id = models.UUIDField(null=True, blank=True)
    reverses = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    memo = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "ledger_journal_entry"
        indexes = [models.Index(fields=["tenant_id", "payment_id"], name="ledger_je_payment_idx")]  # noqa: RUF012

    def __str__(self) -> str:
        return f"{self.template} {self.id}"


class Posting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant_id = models.UUIDField()
    entry = models.ForeignKey(JournalEntry, on_delete=models.PROTECT, related_name="postings")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="postings")
    amount_minor = models.BigIntegerField()  # signed: debit > 0, credit < 0
    currency = models.CharField(max_length=3)

    class Meta:
        db_table = "ledger_posting"
        constraints = [  # noqa: RUF012
            models.CheckConstraint(condition=~models.Q(amount_minor=0), name="ledger_posting_nonzero_chk"),
            models.CheckConstraint(
                condition=models.Q(currency__regex=r"^[A-Z]{3}$"), name="ledger_posting_currency_chk"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.amount_minor} {self.currency}"


class Balance(models.Model):
    """Per (account, currency) balance on the account's normal side. Written only by a DB trigger."""

    id = models.BigAutoField(primary_key=True)
    tenant_id = models.UUIDField()
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="balances")
    currency = models.CharField(max_length=3)
    balance = models.BigIntegerField()
    non_negative = models.BooleanField()
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "ledger_balance"
        constraints = [  # noqa: RUF012
            models.UniqueConstraint(fields=["account", "currency"], name="ledger_balance_account_ccy_uniq"),
            models.CheckConstraint(
                condition=models.Q(non_negative=False) | models.Q(balance__gte=0),
                name="ledger_balance_non_negative_chk",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.balance} {self.currency}"
