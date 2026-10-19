"""GET /v1/epochs/current and POST /v1/oprf/evaluate: spec shape, proof verification, epoch, quota."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from apps.oprf.epochs import epoch_bounds, epoch_number
from apps.oprf.models import QuotaUsage
from apps.oprf.payee import payee_input
from apps.oprf.port import OprfClient

from .conftest import PAYEE_IBAN, MemberHandle, problem, validate_spec

pytestmark = pytest.mark.django_db

EVAL = "/v1/oprf/evaluate"


def _epoch(m: MemberHandle) -> dict[str, Any]:
    response = m.call("GET", "/v1/epochs/current")
    assert response.status_code == 200
    body: dict[str, Any] = json.loads(response.content)
    return body


def _blind(pk_b64: str, n: int = 1) -> tuple[OprfClient, Any, list[str]]:
    client = OprfClient(base64.b64decode(pk_b64))
    state, blinded = client.blind([payee_input("GB", PAYEE_IBAN) + bytes([i]) for i in range(n)])
    return client, state, [base64.b64encode(b).decode() for b in blinded]


def test_epoch_current_matches_spec(make_member: Callable[..., MemberHandle]) -> None:
    body = _epoch(make_member())
    validate_spec("openapi-intel.yaml#/components/schemas/Epoch", body)
    now = datetime.now(UTC)
    assert body["epoch"] == epoch_number(now)
    start, end = epoch_bounds(body["epoch"])
    assert (end - start).days == 7
    assert start <= now < end


def test_epoch_key_stable_within_epoch(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    assert _epoch(m)["oprf_public_key"] == _epoch(m)["oprf_public_key"]


def test_evaluate_round_trip_verifies_against_pk_e(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    epoch = _epoch(m)
    client, state, blinded = _blind(epoch["oprf_public_key"], n=3)
    response = m.call("POST", EVAL, {"epoch": epoch["epoch"], "blinded": blinded})
    assert response.status_code == 200, response.content
    body = json.loads(response.content)
    validate_spec("openapi-intel.yaml#/components/schemas/EvaluateResponse", body)
    assert body["quota_remaining"] == 500 - 3
    outputs = client.finalize(
        state, [base64.b64decode(e) for e in body["evaluated"]], base64.b64decode(body["proof"])
    )
    assert len(outputs) == 3
    assert len(set(outputs)) == 3


def test_wrong_epoch_rejected(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    epoch = _epoch(m)
    _, _, blinded = _blind(epoch["oprf_public_key"])
    problem(m.call("POST", EVAL, {"epoch": epoch["epoch"] - 1, "blinded": blinded}), 409, "UNKNOWN_EPOCH")


@pytest.mark.parametrize(
    "body",
    [
        {"epoch": 1, "blinded": []},
        {"epoch": 1, "blinded": ["not-base64"]},
        {"epoch": 1, "blinded": ["A" * 43 + "="] * 17},
        {"epoch": 1},
        {"epoch": 1, "blinded": ["A" * 43 + "="], "extra": 1},
    ],
)
def test_malformed_body_rejected(make_member: Callable[..., MemberHandle], body: dict[str, Any]) -> None:
    problem(make_member().call("POST", EVAL, body), 422, "VALIDATION_FAILED")


def test_invalid_group_element_rejected_without_using_quota(
    make_member: Callable[..., MemberHandle],
) -> None:
    m = make_member()
    epoch = _epoch(m)
    bad = base64.b64encode(b"\xff" * 32).decode()
    problem(m.call("POST", EVAL, {"epoch": epoch["epoch"], "blinded": [bad]}), 422, "VALIDATION_FAILED")
    assert not QuotaUsage.objects.filter(member=m.member, count__gt=0).exists()


def test_quota_exceeded_returns_429(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    epoch = _epoch(m)
    QuotaUsage.objects.create(member=m.member, day=datetime.now(UTC).date(), count=498)
    _, _, two = _blind(epoch["oprf_public_key"], n=2)
    ok = m.call("POST", EVAL, {"epoch": epoch["epoch"], "blinded": two})
    assert ok.status_code == 200
    assert json.loads(ok.content)["quota_remaining"] == 0
    _, _, one = _blind(epoch["oprf_public_key"], n=1)
    problem(m.call("POST", EVAL, {"epoch": epoch["epoch"], "blinded": one}), 429, "QUOTA_EXCEEDED")
    assert QuotaUsage.objects.get(member=m.member).count == 500


def test_quota_counts_each_blinded_element_and_is_per_member(
    make_member: Callable[..., MemberHandle],
) -> None:
    a, b = make_member(), make_member()
    epoch = _epoch(a)
    QuotaUsage.objects.create(member=a.member, day=datetime.now(UTC).date(), count=495)
    _, _, six = _blind(epoch["oprf_public_key"], n=6)
    problem(a.call("POST", EVAL, {"epoch": epoch["epoch"], "blinded": six}), 429, "QUOTA_EXCEEDED")
    assert b.call("POST", EVAL, {"epoch": epoch["epoch"], "blinded": six}).status_code == 200
