"""Daily statement generation: closing = opening + sum(CREDIT) - sum(DEBIT), booked lines only."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum

from apps.accounts.models import Account, Entry
from apps.statements.models import Statement
from safepay_bank import clock
from safepay_bank.keys import attestation_key
from safepay_bank.problems import ProblemError
from safepay_bank.signing import sign_document


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=UTC)
    return start, start + timedelta(days=1)


def _net_before(account: Account, start: datetime) -> int:
    agg = Entry.objects.filter(account=account, booked_at__lt=start).aggregate(
        credits=Sum("amount_minor", filter=Q(direction=Entry.Direction.CREDIT)),
        debits=Sum("amount_minor", filter=Q(direction=Entry.Direction.DEBIT)),
    )
    return int(agg["credits"] or 0) - int(agg["debits"] or 0)


def _line(entry: Entry) -> dict[str, Any]:
    line: dict[str, Any] = {
        "line_id": str(entry.line_id),
        "end_to_end_id": str(entry.end_to_end_id),
        "transfer_id": str(entry.transfer_id),
        "direction": entry.direction,
        "amount_minor": entry.amount_minor,
        "counterparty": entry.counterparty,
        "booked_at": clock.rfc3339(entry.booked_at),
    }
    if entry.remittance_info:
        line["remittance_info"] = entry.remittance_info
    return line


def build_statement(account: Account, day: date) -> dict[str, Any]:
    start, end = _day_bounds(day)
    opening = _net_before(account, start)
    entries = list(
        Entry.objects.filter(account=account, booked_at__gte=start, booked_at__lt=end).order_by("seq")
    )
    closing = opening
    for e in entries:
        closing += e.amount_minor if e.direction == Entry.Direction.CREDIT else -e.amount_minor
    document: dict[str, Any] = {
        "statement_id": str(uuid.uuid4()),
        "account_id": str(account.account_id),
        "iban": account.iban,
        "date": day.isoformat(),
        "currency": account.currency,
        "opening_balance_minor": opening,
        "closing_balance_minor": closing,
        "lines": [_line(e) for e in entries],
        "bank_id": settings.SAFEPAY_BANK_ID,
        "simulated": True,
    }
    key_id, key = attestation_key()
    return sign_document(document, key, key_id)


def get_statement(account_id: uuid.UUID, day: date) -> dict[str, Any]:
    account = Account.objects.filter(pk=account_id).first()
    if account is None:
        raise ProblemError("ACCOUNT_NOT_FOUND", "account not found")
    today = clock.now().date()
    if day > today:
        raise ProblemError(
            "VALIDATION_FAILED",
            "date is in the future",
            errors=[{"loc": ["query", "date"], "msg": "future date"}],
        )
    final = day < today
    if final:
        issued = Statement.objects.filter(account=account, date=day, final=True).first()
        if issued is not None:
            return dict(issued.document)
    document = build_statement(account, day)
    try:
        with transaction.atomic():
            Statement.objects.create(
                statement_id=uuid.UUID(document["statement_id"]),
                account=account,
                date=day,
                final=final,
                document=document,
            )
    except IntegrityError:
        issued = Statement.objects.get(account=account, date=day, final=True)
        return dict(issued.document)
    return document
