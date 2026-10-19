"""GET /v1/snapshots/{epoch}: signed, pk_e pinned, 16-byte tags matching a member's blind query."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import pytest
from django.conf import settings

from apps.oprf.payee import payee_input
from apps.oprf.port import OprfClient
from apps.reports.models import Report
from apps.snapshots.models import Snapshot
from safepay_intel.crypto import public_key_from_raw, verify_object

from .conftest import OTHER_PAYEE_IBAN, PAYEE_IBAN, Keys, MemberHandle, problem, raw_pub, validate_spec

pytestmark = pytest.mark.django_db

MakeMember = Callable[..., MemberHandle]
MakeReceipt = Callable[..., dict[str, Any]]


def _epoch(m: MemberHandle) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(m.call("GET", "/v1/epochs/current").content)
    return body


def _snapshot(m: MemberHandle, epoch: int) -> dict[str, Any]:
    response = m.call("GET", f"/v1/snapshots/{epoch}")
    assert response.status_code == 200, response.content
    body: dict[str, Any] = json.loads(response.content)
    return body


def _blind_tag(m: MemberHandle, epoch: dict[str, Any], country: str, iban: str) -> str:
    """What the core intel client does: blind x, evaluate, verify proof against pk_e, finalize, truncate."""
    client = OprfClient(base64.b64decode(epoch["oprf_public_key"]))
    state, blinded = client.blind([payee_input(country, iban)])
    response = m.call(
        "POST",
        "/v1/oprf/evaluate",
        {"epoch": epoch["epoch"], "blinded": [base64.b64encode(b).decode() for b in blinded]},
    )
    body = json.loads(response.content)
    out = client.finalize(
        state, [base64.b64decode(e) for e in body["evaluated"]], base64.b64decode(body["proof"])
    )
    return out[0][:16].hex()


def _report(m: MemberHandle, make_receipt: MakeReceipt, iban: str = PAYEE_IBAN) -> dict[str, Any]:
    report = m.report(make_receipt(m, payee_iban=iban))
    assert m.call("POST", "/v1/reports", report).status_code == 201
    return report


def test_snapshot_signed_and_pins_pk_e(make_member: MakeMember, throwaway_keys: Keys) -> None:
    m = make_member()
    epoch = _epoch(m)
    snap = _snapshot(m, epoch["epoch"])
    validate_spec("schemas/Snapshot.json", snap)
    assert snap["oprf_public_key"] == epoch["oprf_public_key"]
    assert snap["tag_bytes"] == 16
    assert snap["key_id"] == settings.SAFEPAY_INTEL_SNAPSHOT_KEY_ID
    key = public_key_from_raw(raw_pub(throwaway_keys.snapshot))
    assert verify_object(key, snap)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("oprf_public_key", "B" * 43 + "="),
        ("entries", []),
        ("seq", 99),
        ("valid_until", "2099-01-01T00:00:00Z"),
    ],
)
def test_tampered_snapshot_fails_verification(
    make_member: MakeMember, make_receipt: MakeReceipt, throwaway_keys: Keys, field: str, value: Any
) -> None:
    m = make_member()
    _report(m, make_receipt)
    snap = _snapshot(m, _epoch(m)["epoch"])
    snap[field] = value
    assert not verify_object(public_key_from_raw(raw_pub(throwaway_keys.snapshot)), snap)


def test_reported_payee_tag_matches_blind_query(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    reporter, querier = make_member(), make_member()
    _report(reporter, make_receipt)
    epoch = _epoch(querier)
    snap = _snapshot(querier, epoch["epoch"])
    tags = {e["tag"]: e for e in snap["entries"]}
    hit = _blind_tag(querier, epoch, "GB", PAYEE_IBAN)
    miss = _blind_tag(querier, epoch, "DE", OTHER_PAYEE_IBAN)
    assert len(hit) == 32
    assert hit in tags
    assert tags[hit]["distinct_reporters"] == 1
    assert miss not in tags


def test_distinct_reporters_and_retraction(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    a, b = make_member(), make_member()
    ra = _report(a, make_receipt)
    _report(b, make_receipt)
    epoch = _epoch(a)
    snap = _snapshot(a, epoch["epoch"])
    assert [e["distinct_reporters"] for e in snap["entries"]] == [2]
    assert a.call("DELETE", f"/v1/reports/{ra['report_id']}").status_code == 204
    after = _snapshot(a, epoch["epoch"])
    assert after["seq"] == snap["seq"] + 1
    assert [e["distinct_reporters"] for e in after["entries"]] == [1]
    assert Snapshot.objects.filter(epoch=epoch["epoch"]).count() == 2  # history kept (append-only)


def test_unchanged_snapshot_is_reused(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    _report(m, make_receipt)
    epoch = _epoch(m)["epoch"]
    first, second = _snapshot(m, epoch), _snapshot(m, epoch)
    assert first == second


def test_expired_reports_drop_out(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    from datetime import UTC, datetime, timedelta  # noqa: PLC0415

    m = make_member()
    _report(m, make_receipt)
    epoch = _epoch(m)["epoch"]
    assert len(_snapshot(m, epoch)["entries"]) == 1
    Report.objects.update(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert _snapshot(m, epoch)["entries"] == []


def test_unknown_epochs_not_found(make_member: MakeMember) -> None:
    m = make_member()
    epoch = _epoch(m)["epoch"]
    problem(m.call("GET", f"/v1/snapshots/{epoch + 1}"), 404, "NOT_FOUND")
    problem(m.call("GET", f"/v1/snapshots/{epoch - 1}"), 404, "NOT_FOUND")


def test_snapshot_reveals_no_payee_identifier(make_member: MakeMember, make_receipt: MakeReceipt) -> None:
    m = make_member()
    _report(m, make_receipt)
    raw = m.call("GET", f"/v1/snapshots/{_epoch(m)['epoch']}").content.decode()
    assert PAYEE_IBAN not in raw
    assert PAYEE_IBAN[4:] not in raw
