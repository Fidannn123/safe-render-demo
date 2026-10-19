"""Operator-side privacy attack and the OPRF power-on self-test (D-048, D-049). SYNTHETIC IBANs only."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest
from django.conf import settings

from apps.oprf import selftest
from apps.oprf.adversary import (
    Payee,
    Query,
    attack,
    naive_control,
    parse_transcript,
    payee_input,
    synthetic_candidates,
)
from apps.oprf.payee import iban_valid
from apps.oprf.port import OprfClient, OprfServer
from safepay_intel.crypto import b64decode_strict

DOMAIN = "safepay/v1/payee"
TRUE = [Payee("GB", "GB82WEST12345698765432"), Payee("DE", "DE89370400440532013000")]


def _transcript(server: OprfServer, payees: list[Payee], repeats: int = 2) -> list[str]:
    """What the operator captures: each payee looked up `repeats` times by an honest client."""
    client = OprfClient(server.public_key)
    lines = []
    for _ in range(repeats):
        for p in payees:
            _, blinded = client.blind([payee_input(DOMAIN, p)])
            body = {"epoch": 7, "blinded": [base64.b64encode(b).decode() for b in blinded]}
            lines.append(f"POST /v1/oprf/evaluate {json.dumps(body, sort_keys=True)}")
    return lines


def _candidates(extra: int) -> list[Payee]:
    return TRUE + list(synthetic_candidates(TRUE, extra, {p.iban for p in TRUE}))


def test_synthetic_candidates_are_valid_and_distinct() -> None:
    made = list(synthetic_candidates(TRUE, 500, {p.iban for p in TRUE}))
    assert len({p.iban for p in made}) == 500
    assert all(iban_valid(p.iban) for p in made)
    assert not {p.iban for p in made} & {p.iban for p in TRUE}


def test_operator_with_key_and_candidates_recovers_nothing() -> None:
    server = OprfServer(os.urandom(32), b"epoch-7")
    queries = parse_transcript(_transcript(server, TRUE), b64decode_strict)
    result = attack(queries, _candidates(2000), {7: server}, DOMAIN)
    assert result["candidates_checked"] == 2002
    assert result["recovered_identifiers"] == 0
    assert result["repeated_blinded_elements"] == 0  # repeated lookups of one payee are unlinkable


def test_control_naive_hash_lookup_is_fully_recovered() -> None:
    server = OprfServer(os.urandom(32), b"epoch-7")
    control = naive_control(TRUE, _candidates(2000), {7: server}, DOMAIN)
    assert control["recovered_identifiers"] == len(TRUE)


def test_attack_detects_a_client_that_leaks_the_prf_output_or_the_iban() -> None:
    server = OprfServer(os.urandom(32), b"epoch-7")
    tag_leak = Query(epoch=-1, blinded=(server.evaluate(payee_input(DOMAIN, TRUE[0]))[:32],), text="")
    text_leak = Query(epoch=-1, blinded=(), text=f"POST /v1/oprf/evaluate {TRUE[1].iban}")
    result = attack([tag_leak, text_leak], _candidates(100), {7: server}, DOMAIN)
    assert result["recovered_identifiers"] == 2


def test_power_on_self_test_passes_on_the_rfc_vectors() -> None:
    assert selftest.run_self_test(Path(settings.SAFEPAY_SPEC_DIR)) == 3


def test_power_on_self_test_fails_closed_on_a_wrong_vector(tmp_path: Path) -> None:
    source = Path(settings.SAFEPAY_SPEC_DIR) / "vectors" / "rfc9497" / "allVectors.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    for suite in data:
        if suite["identifier"] == selftest.SUITE and suite["mode"] == selftest.VOPRF_MODE:
            first = suite["vectors"][0]
            first["Output"] = ("00" if first["Output"][:2] != "00" else "11") + first["Output"][2:]
    target = tmp_path / "vectors" / "rfc9497"
    target.mkdir(parents=True)
    (target / "allVectors.json").write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(selftest.SelfTestError):
        selftest.run_self_test(tmp_path)
