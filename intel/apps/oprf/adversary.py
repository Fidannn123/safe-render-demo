"""Privacy attack on the blind lookup (docs/architecture.md section 2.7): the network operator tries to
learn which payees members looked up.

The adversary holds everything the operator has: the full request transcript and every epoch's OPRF
key. It also holds a list of candidate IBANs (the true payees plus synthetic ones, 10^6 by default). For
each candidate x it computes what the operator can compute: the PRF output F_k(x) for each epoch (and its
16-byte snapshot tag), SHA-256(x), and the IBAN text. A query is recovered if any of these appears in
what the client sent (blinded elements) or in the operator's own answers (k * B). Repeated blinded
elements would also let it link queries for the same payee, so those are counted too.

A positive control runs the same attack on a naive design that sends SHA-256(x): the attack must
recover every true payee there, which shows it is strong enough to notice a leak. SYNTHETIC data only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass

from .port import OprfServer

_IBAN_TEXT = re.compile(r"[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}")


@dataclass(frozen=True)
class Payee:
    country: str
    iban: str


def _check_digits(country: str, bban: str) -> str:
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    return f"{98 - int(digits) % 97:02d}"


def synthetic_candidates(templates: Sequence[Payee], count: int, exclude: set[str]) -> Iterator[Payee]:
    """Deterministic, mod-97-valid IBANs shaped like the templates (same country and length)."""
    made, i = 0, 0
    while made < count:
        template = templates[i % len(templates)]
        width = len(template.iban) - 4
        bban = str(i).rjust(width, "0")[-width:]
        iban = template.country + _check_digits(template.country, bban) + bban
        i += 1
        if iban in exclude:
            continue
        exclude.add(iban)
        made += 1
        yield Payee(template.country, iban)


def payee_input(domain: str, payee: Payee) -> bytes:
    return f"{domain}|{payee.country}|{payee.iban}".encode()


@dataclass(frozen=True)
class Query:
    epoch: int
    blinded: tuple[bytes, ...]
    text: str


def parse_transcript(lines: Iterable[str], decode: Callable[[str], bytes]) -> list[Query]:
    """Captured `POST /v1/oprf/evaluate {json}` lines; other intel requests are ignored."""
    queries = []
    for line in lines:
        method, target, body = ([*line.split(" ", 2), "", ""])[:3]
        if method != "POST" or not target.startswith("/v1/oprf/evaluate") or not body:
            continue
        payload = json.loads(body)
        blinded = tuple(decode(b) for b in payload["blinded"])
        queries.append(Query(epoch=int(payload["epoch"]), blinded=blinded, text=line))
    return queries


def _observed(queries: Sequence[Query], keys: dict[int, OprfServer]) -> tuple[set[bytes], int]:
    """Every byte string the operator sees or computes for the queries, and repeated blinded elements."""
    seen: set[bytes] = set()
    blinded_all: list[bytes] = []
    for q in queries:
        blinded_all.extend(q.blinded)
        seen.update(q.blinded)
        if q.epoch in keys:
            seen.update(keys[q.epoch].blind_evaluate(list(q.blinded)).evaluated)
    seen |= {value[:16] for value in seen}
    return seen, len(blinded_all) - len(set(blinded_all))


def _fingerprints(x: bytes, keys: dict[int, OprfServer]) -> Iterator[bytes]:
    yield hashlib.sha256(x).digest()
    for server in keys.values():
        output = server.evaluate(x)
        yield output[:32]
        yield output[32:]
        yield output[:16]  # the snapshot tag


def attack(
    queries: Sequence[Query],
    candidates: Iterable[Payee],
    keys: dict[int, OprfServer],
    domain: str,
) -> dict[str, int]:
    seen, repeated = _observed(queries, keys)
    texts = {m for q in queries for m in _IBAN_TEXT.findall(q.text)}
    recovered, checked = set(), 0
    for payee in candidates:
        checked += 1
        x = payee_input(domain, payee)
        if payee.iban in texts or any(f in seen for f in _fingerprints(x, keys)):
            recovered.add(payee.iban)
    return {
        "candidates_checked": checked,
        "queries": len(queries),
        "blinded_elements": sum(len(q.blinded) for q in queries),
        "repeated_blinded_elements": repeated,
        "recovered_identifiers": len(recovered),
    }


def naive_control(
    true_payees: Sequence[Payee], candidates: Iterable[Payee], keys: dict[int, OprfServer], domain: str
) -> dict[str, int]:
    """The same attack() on a design whose client sends SHA-256(x): it must recover every true payee."""
    queries = [
        Query(epoch=-1, blinded=(hashlib.sha256(payee_input(domain, p)).digest(),), text="")
        for p in true_payees
    ]
    return attack(queries, candidates, keys, domain)
