"""Account services for the SIMULATED bank: seeding, deposits, serialisation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from django.db import transaction

from apps.accounts.iban import iban_valid, make_iban
from apps.accounts.models import Account, Entry
from safepay_bank import clock

FUNDING_NAMESPACE = uuid.UUID("5afe9a7e-0000-4000-8000-00000000f00d")
FUNDING_COUNTERPARTY: dict[str, Any] = {
    "country": "GB",
    "iban": make_iban("GB", "SPSB00000000000001"),
    "name": "SAFEPAY-SANDBOX FUNDING (SIMULATED)",
}


def account_json(account: Account) -> dict[str, Any]:
    return {
        "account_id": str(account.account_id),
        "iban": account.iban,
        "currency": account.currency,
        "owner_name": account.owner_name,
        "balance_minor": account.balance_minor,
        "simulated": True,
    }


def deposit(
    account: Account,
    amount_minor: int,
    *,
    end_to_end_id: uuid.UUID,
    counterparty: dict[str, Any] | None = None,
    remittance_info: str = "",
    booked_at: datetime | None = None,
) -> Entry:
    """Book a CREDIT from outside the sandbox (synthetic funding or customer receipt). Idempotent on e2e."""
    if amount_minor <= 0:
        raise ValueError("amount_minor must be positive")
    with transaction.atomic():
        locked = Account.objects.select_for_update().get(pk=account.pk)
        existing = Entry.objects.filter(
            account=locked, end_to_end_id=end_to_end_id, direction=Entry.Direction.CREDIT
        ).first()
        if existing is not None:
            return existing
        entry = Entry.objects.create(
            account=locked,
            transfer_id=uuid.uuid5(FUNDING_NAMESPACE, f"transfer:{end_to_end_id}"),
            end_to_end_id=end_to_end_id,
            direction=Entry.Direction.CREDIT,
            amount_minor=amount_minor,
            counterparty=counterparty or FUNDING_COUNTERPARTY,
            remittance_info=remittance_info,
            booked_at=booked_at or clock.now(),
        )
        locked.balance_minor += amount_minor
        locked.save(update_fields=["balance_minor"])
        account.balance_minor = locked.balance_minor
        return entry


def open_account(
    *,
    account_id: uuid.UUID,
    iban: str,
    owner_name: str,
    currency: str = "USD",
    opening_balance_minor: int = 0,
    booked_at: datetime | None = None,
) -> Account:
    """Create (or return) a SIMULATED account; the opening balance is a funding CREDIT line."""
    if not iban_valid(iban):
        raise ValueError(f"invalid IBAN {iban}")
    with transaction.atomic():
        account, _ = Account.objects.get_or_create(
            account_id=account_id,
            defaults={"iban": iban, "owner_name": owner_name[:140], "currency": currency},
        )
        if opening_balance_minor > 0:
            deposit(
                account,
                opening_balance_minor,
                end_to_end_id=uuid.uuid5(FUNDING_NAMESPACE, f"opening:{account_id}"),
                remittance_info="Opening balance (SIMULATED)",
                booked_at=booked_at,
            )
        return account
