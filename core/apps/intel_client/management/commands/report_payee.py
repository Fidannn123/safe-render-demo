"""Report a payee to the fraud network with a bank-signed receipt (demo of the accountable report path).

SYNTHETIC: the reporting business first "loses" money to the payee in the SIMULATED bank, from its own
bank account, so the report carries a genuine bank-signed SETTLED receipt; intel admits it only then.
`--retract` withdraws a report (only its reporter can). Prints JSON; never prints key material.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser

from apps.bank_adapter.client import SignedBankClient
from apps.bank_adapter.signing import RemoteError
from apps.evidence.seeding import legacy_account_id
from apps.intel_client.service import default_client
from apps.ledger.tenancy import tenant_transaction
from apps.tenants.models import Tenant


class Command(BaseCommand):
    help = "File (or --retract) a bank-attested fraud report for a payee IBAN. SYNTHETIC demo path."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", type=uuid.UUID, required=True, help="reporting tenant (member)")
        parser.add_argument("--iban", default="")
        parser.add_argument("--name", default="Reported payee (SYNTHETIC)")
        parser.add_argument("--amount-minor", type=int, default=25000)
        parser.add_argument("--category", default="INVOICE_REDIRECTION")
        parser.add_argument("--retract", default="", help="report_id to withdraw")

    def handle(self, *args: Any, **options: Any) -> None:
        tenant_id: uuid.UUID = options["tenant"]
        client = default_client(tenant_id)
        if options["retract"]:
            try:
                client.transport.request("DELETE", f"/v1/reports/{uuid.UUID(options['retract'])}")
            except RemoteError as exc:
                raise CommandError(f"retraction refused: {exc.problem_code or exc.code}") from exc
            self.stdout.write(json.dumps({"retracted": options["retract"]}))
            return
        iban = "".join(str(options["iban"]).split()).upper()
        if not iban:
            raise CommandError("--iban is required")
        with tenant_transaction(tenant_id):
            Tenant.objects.get(id=tenant_id)
        # The earlier loss left from the member's legacy account (seeded, owned by the member, not the
        # account core reconciles), so the demo does not trip the reconciler's MISSING_IN_LEDGER freeze.
        account_id = legacy_account_id(tenant_id)
        transfer = SignedBankClient().submit(
            {
                "end_to_end_id": str(uuid.uuid4()),
                "debtor_account_id": str(account_id),
                "creditor": {"country": iban[:2], "iban": iban, "name": options["name"]},
                "amount_minor": options["amount_minor"],
                "currency": "USD",
                "remittance_info": "SYNTHETIC: payment later found fraudulent",
            }
        )
        report_id = uuid.uuid4()
        evidence = hashlib.sha256(f"synthetic local evidence {report_id}".encode()).hexdigest()
        try:
            client.submit_report(tenant_id, transfer["receipt"], evidence, options["category"], report_id)
        except RemoteError as exc:
            raise CommandError(f"report refused: {exc.problem_code or exc.code}") from exc
        self.stdout.write(
            json.dumps({"report_id": str(report_id), "receipt_id": transfer["receipt"]["receipt_id"]})
        )
