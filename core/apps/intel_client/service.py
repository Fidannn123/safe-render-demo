"""Blind network queries: only randomized VOPRF elements leave the tenant; lookup stays local."""

from __future__ import annotations

import base64
import os
import re
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from django.conf import settings
from django.utils import timezone

from apps.bank_adapter.contracts import validate_document
from apps.bank_adapter.signing import (
    RemoteError,
    SignedTransport,
    load_private,
    load_public,
    sign_object,
    verify_object,
)
from apps.guardian.inputs import NetworkResult

from .oprf import OprfClient


def payee_input(country: str, iban: str) -> bytes:
    normal = "".join(iban.split()).upper()
    country = country.upper()
    if not re.fullmatch(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}", normal) or country != normal[:2]:
        raise ValueError("invalid payee")
    digits = "".join(str(int(char, 36)) for char in normal[4:] + normal[:4])
    if int(digits) % 97 != 1:
        raise ValueError("invalid payee")
    return f"safepay/v1/payee|{country}|{normal}".encode()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(UTC)


class IntelClient:
    def __init__(
        self,
        transport: SignedTransport,
        snapshot_key: Ed25519PublicKey,
        snapshot_key_id: str = "intel:snapshot:1",
    ) -> None:
        self.transport, self.snapshot_key, self.snapshot_key_id = transport, snapshot_key, snapshot_key_id
        self._latest: dict[int, int] = {}

    def lookup(self, iban: str, country: str | None = None, now: datetime | None = None) -> NetworkResult:
        """All failures return UNAVAILABLE; callers apply the frozen new-payee HOLD policy."""
        try:
            return self._lookup(iban, country or iban[:2].upper(), now or timezone.now())
        except Exception:
            return NetworkResult(status="UNAVAILABLE")

    def _lookup(self, iban: str, country: str, now: datetime) -> NetworkResult:
        private_input = payee_input(country, iban)
        epoch = self.transport.request("GET", "/v1/epochs/current")
        epoch_number = int(epoch["epoch"])
        snapshot = self.transport.request("GET", f"/v1/snapshots/{epoch_number}")
        validate_document("Snapshot", snapshot)
        verify_object(snapshot, self.snapshot_key, self.snapshot_key_id)
        if (
            snapshot["epoch"] != epoch_number
            or snapshot["suite"] != "ristretto255-SHA512"
            or snapshot["mode"] != "VOPRF"
            or snapshot["tag_bytes"] != 16
            or snapshot["oprf_public_key"] != epoch["oprf_public_key"]
            or timestamp(snapshot["issued_at"]) > now
            or timestamp(snapshot["valid_until"]) <= now
            or timestamp(epoch["starts_at"]) > now
            or timestamp(epoch["ends_at"]) <= now
            or snapshot["seq"] < self._latest.get(epoch_number, -1)
        ):
            raise RemoteError("SNAPSHOT_INVALID")
        self._latest[epoch_number] = snapshot["seq"]
        oprf = OprfClient(base64.b64decode(snapshot["oprf_public_key"], validate=True))
        state, blinded = oprf.blind([private_input])
        evaluated = self.transport.request(
            "POST",
            "/v1/oprf/evaluate",
            {
                "epoch": epoch_number,
                "blinded": [base64.b64encode(value).decode("ascii") for value in blinded],
            },
        )
        if evaluated["epoch"] != epoch_number:
            raise RemoteError("EPOCH_MISMATCH")
        output = oprf.finalize(
            state,
            [base64.b64decode(value, validate=True) for value in evaluated["evaluated"]],
            base64.b64decode(evaluated["proof"], validate=True),
        )[0]
        tag = output[:16].hex()
        entries = snapshot["entries"]
        tags = [entry["tag"] for entry in entries]
        if len(tags) != len(set(tags)):
            raise RemoteError("SNAPSHOT_INVALID")
        reporters = max(
            (
                entry["distinct_reporters"]
                for entry in entries
                if entry["tag"] == tag and timestamp(entry["expires_at"]) > now
            ),
            default=0,
        )
        return NetworkResult(status="HIT" if reporters else "MISS", reporters=reporters, epoch=epoch_number)

    def submit_report(
        self,
        member_id: uuid.UUID,
        receipt: dict[str, Any],
        evidence_sha256: str,
        category: str = "OTHER_FRAUD",
        report_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Explicit reports reveal the reported payee by design; queries never do."""
        validate_document("Receipt", receipt)
        directory = Path(settings.SAFEPAY_KEYS_DIR) / "ed25519"
        verify_object(receipt, load_public(directory / "bank_attestation.pub"), "bank:attestation:1")
        if receipt["status"] != "SETTLED":
            raise RemoteError("RECEIPT_INVALID")
        report = sign_object(
            {
                "report_id": str(report_id or uuid.uuid4()),
                "member_id": str(member_id),
                "payee": receipt["creditor"],
                "category": category,
                "receipt": receipt,
                "evidence_sha256": evidence_sha256,
                "created_at": timezone.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "nonce": secrets.token_hex(16),
                "key_id": self.transport.key_id,
            },
            self.transport.key,
        )
        validate_document("Report", report)
        return self.transport.request("POST", "/v1/reports", report)


def member_key_path(tenant_id: uuid.UUID) -> Path:
    directory = os.getenv(
        "SAFEPAY_MEMBER_KEYS_DIR", str(Path(settings.SAFEPAY_KEYSTORE_DIR).parent / "members")
    )
    return Path(directory) / f"{tenant_id}.pem"


def default_client(tenant_id: uuid.UUID) -> IntelClient:
    transport = SignedTransport(
        settings.SAFEPAY_INTEL_URL, load_private(member_key_path(tenant_id)), f"member-{tenant_id}:1"
    )
    public_path = Path(
        os.getenv(
            "SAFEPAY_INTEL_SNAPSHOT_PUB_FILE",
            str(Path(settings.SAFEPAY_KEYS_DIR) / "ed25519" / "intel_snapshot.pub"),
        )
    )
    return IntelClient(
        transport, load_public(public_path), os.getenv("SAFEPAY_INTEL_SNAPSHOT_KEY_ID", "intel:snapshot:1")
    )


def get_network_result(tenant_id: uuid.UUID, iban: str) -> NetworkResult:
    try:
        return default_client(tenant_id).lookup(iban)
    except Exception:
        return NetworkResult(status="UNAVAILABLE")
