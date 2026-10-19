"""Member request signatures (D-012) on every /v1 endpoint."""

from __future__ import annotations

import json
import secrets
import time
from collections.abc import Callable
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.test import Client

from apps.members.models import Member
from safepay_intel.crypto import request_signing_string

from .conftest import MemberHandle, b64, problem

pytestmark = pytest.mark.django_db

PATH = "/v1/epochs/current"


def _mismatched_call(
    m: MemberHandle, method: str, path: str, body: bytes, *, signed_path: str, signed_body: bytes
) -> Any:
    ts, nonce = str(int(time.time())), secrets.token_hex(16)
    sig = b64(m.key.sign(request_signing_string(method, signed_path, ts, nonce, signed_body)))
    headers = {
        "X-SafePay-Key-Id": m.key_id,
        "X-SafePay-Timestamp": ts,
        "X-SafePay-Nonce": nonce,
        "X-SafePay-Signature": sig,
    }
    return m.client.generic(method, path, data=body, content_type="application/json", headers=headers)


def test_signed_request_accepted(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    assert m.call("GET", PATH).status_code == 200


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", PATH),
        ("POST", "/v1/oprf/evaluate"),
        ("GET", "/v1/snapshots/0"),
        ("POST", "/v1/reports"),
        ("DELETE", "/v1/reports/abababab-cdcd-4efe-8f0f-121212121212"),
    ],
)
def test_missing_headers_rejected_everywhere(client: Client, method: str, path: str) -> None:
    problem(
        client.generic(method, path, data=b"{}", content_type="application/json"), 401, "SIGNATURE_INVALID"
    )


def test_wrong_key_signature_rejected(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    problem(m.call("GET", PATH, sign_key=Ed25519PrivateKey.generate()), 401, "SIGNATURE_INVALID")


def test_unknown_key_id_rejected(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    problem(m.call("GET", PATH, key_id="member-unknown:1"), 401, "SIGNATURE_INVALID")


def test_other_members_key_id_rejected(make_member: Callable[..., MemberHandle]) -> None:
    a, b = make_member(), make_member()
    problem(a.call("GET", PATH, key_id=b.key_id), 401, "SIGNATURE_INVALID")


@pytest.mark.parametrize("delta", [-301, 301])
def test_timestamp_outside_skew_rejected(make_member: Callable[..., MemberHandle], delta: int) -> None:
    m = make_member()
    problem(m.call("GET", PATH, timestamp=int(time.time()) + delta), 401, "REQUEST_EXPIRED")


def test_nonce_replay_rejected(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    nonce = "ab" * 16
    assert m.call("GET", PATH, nonce=nonce).status_code == 200
    problem(m.call("GET", PATH, nonce=nonce), 401, "NONCE_REPLAYED")


def test_body_is_signed(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    sent = json.dumps({"epoch": 0, "blinded": ["A" * 43 + "="]}).encode()
    response = _mismatched_call(
        m, "POST", "/v1/oprf/evaluate", sent, signed_path="/v1/oprf/evaluate", signed_body=b"{}"
    )
    problem(response, 401, "SIGNATURE_INVALID")


def test_path_is_signed(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    response = _mismatched_call(m, "GET", PATH, b"", signed_path="/v1/snapshots/0", signed_body=b"")
    problem(response, 401, "SIGNATURE_INVALID")


def test_suspended_member_rejected(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    Member.objects.filter(pk=m.member.pk).update(status=Member.Status.SUSPENDED)
    problem(m.call("GET", PATH), 403, "MEMBER_SUSPENDED")


def test_signed_but_malformed_json_is_validation_failed(make_member: Callable[..., MemberHandle]) -> None:
    m = make_member()
    sent = b"{not json"
    response = _mismatched_call(
        m, "POST", "/v1/oprf/evaluate", sent, signed_path="/v1/oprf/evaluate", signed_body=sent
    )
    problem(response, 422, "VALIDATION_FAILED")


def test_every_spec_operation_is_routed() -> None:
    import yaml  # noqa: PLC0415
    from django.conf import settings  # noqa: PLC0415

    from safepay_intel.api import api  # noqa: PLC0415

    spec = yaml.safe_load((settings.SAFEPAY_SPEC_DIR / "openapi-intel.yaml").read_text(encoding="utf-8"))
    wanted = {
        (path, method, op["operationId"])
        for path, ops in spec["paths"].items()
        if path.startswith("/v1/")
        for method, op in ops.items()
    }
    ours = api.get_openapi_schema(path_prefix="/v1/")["paths"]
    got = {(p, m, op["operationId"]) for p, ops in ours.items() for m, op in ops.items()}
    assert wanted <= got
