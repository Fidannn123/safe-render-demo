"""Admission control: create a network member with its signing key and owned bank accounts.

There is no public registration endpoint (D-009); in production this follows KYB. Example:

  python manage.py create_member --name "Twin Retail (SYNTHETIC)" \\
      --public-key <base64 raw Ed25519> --key-id member-1111:1 \\
      --account 33333333-4444-4555-8666-777777777777:GB33BUKB20201555555555

Prints the member id as JSON. With --member-id the command is idempotent for the same values.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import transaction

from apps.members.auth import KEY_ID_PATTERN
from apps.members.models import Member, MemberAccount, MemberKey
from apps.oprf.payee import iban_valid, normalise_iban
from safepay_intel.crypto import KeyFormatError, b64decode_strict, public_key_from_raw


class Command(BaseCommand):
    help = "Create (admit) an intel network member with an Ed25519 key and owned bank accounts."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--name", required=True)
        parser.add_argument("--member-id", default=None, help="UUID (default: random)")
        parser.add_argument("--key-id", required=True, help="e.g. member-1111:1")
        parser.add_argument("--public-key", required=True, help="raw Ed25519 public key, base64")
        parser.add_argument(
            "--account",
            action="append",
            default=[],
            help="<bank account_id uuid>:<IBAN> owned by the member (repeatable)",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        name: str = options["name"]
        key_id: str = options["key_id"]
        if not KEY_ID_PATTERN.match(key_id):
            raise CommandError("invalid --key-id")
        try:
            raw = b64decode_strict(options["public_key"])
            public_key_from_raw(raw)
        except KeyFormatError as exc:
            raise CommandError(f"invalid --public-key: {exc}") from exc
        try:
            member_id = uuid.UUID(options["member_id"]) if options["member_id"] else uuid.uuid4()
        except ValueError as exc:
            raise CommandError("invalid --member-id") from exc
        accounts: list[tuple[uuid.UUID, str]] = []
        for item in options["account"]:
            acc, _, iban = str(item).partition(":")
            norm = normalise_iban(iban)
            try:
                account_id = uuid.UUID(acc)
            except ValueError as exc:
                raise CommandError(f"invalid account id in {item!r}") from exc
            if not iban_valid(norm):
                raise CommandError(f"invalid IBAN in {item!r}")
            accounts.append((account_id, norm))

        with transaction.atomic():
            member, _ = Member.objects.get_or_create(id=member_id, defaults={"name": name})
            key, created = MemberKey.objects.get_or_create(
                key_id=key_id, defaults={"member": member, "public_key": raw}
            )
            if not created and (key.member_id != member.id or bytes(key.public_key) != raw):
                raise CommandError(f"key_id {key_id} already bound to a different member or key")
            for account_id, iban in accounts:
                acct, made = MemberAccount.objects.get_or_create(
                    account_id=account_id, defaults={"member": member, "iban": iban}
                )
                if not made and (acct.member_id != member.id or acct.iban != iban):
                    raise CommandError(f"account {account_id} already bound to a different member")
        self.stdout.write(json.dumps({"member_id": str(member.id), "key_id": key_id}))
