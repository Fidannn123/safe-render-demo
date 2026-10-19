"""Verify the live chain, sign its uncovered head, and optionally export public evidence."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand, CommandParser

from apps.audit.chain import event_document, verify_chain
from apps.audit.checkpoints import checkpoint, verify_documents
from apps.audit.models import AuditCheckpoint, AuditEvent
from apps.keystore.file_store import default_keystore
from apps.ledger.tenancy import tenant_transaction


class Command(BaseCommand):
    help = "Verify a tenant audit chain and signed checkpoints; optionally export JSON evidence."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--tenant", required=True, type=UUID)
        parser.add_argument("--export", type=Path)

    def handle(self, *args: Any, **options: Any) -> None:
        tenant = options["tenant"]
        with tenant_transaction(tenant):
            verify_chain(tenant)
            checkpoint(tenant, force=True)
            events = [event_document(event) for event in AuditEvent.objects.order_by("seq")]
            checkpoints = [item.document for item in AuditCheckpoint.objects.order_by("seq_to")]
            store = default_keystore()
            count = verify_documents(events, checkpoints, store)
            public_keys = {
                document["key_id"]: store.public_key(document["key_id"])
                .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
                .decode("ascii")
                for document in checkpoints
            }
        if options["export"]:
            directory: Path = options["export"]
            directory.mkdir(parents=True, exist_ok=True)
            for name, documents in (("audit.jsonl", events), ("checkpoints.jsonl", checkpoints)):
                (directory / name).write_text(
                    "".join(json.dumps(doc, sort_keys=True) + "\n" for doc in documents), encoding="utf-8"
                )
            (directory / "audit-public-keys.json").write_text(
                json.dumps(public_keys, sort_keys=True, indent=2) + "\n", encoding="utf-8"
            )
        self.stdout.write(json.dumps({"tenant_id": str(tenant), "events": count, "verified": True}))
