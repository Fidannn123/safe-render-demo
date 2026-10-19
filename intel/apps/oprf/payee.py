"""Payee identifiers and the OPRF input x = input_domain | country | normalised IBAN (spec/policy.yaml).

Encoding (DECISIONS.md D-036): UTF-8 of "<input_domain>|<COUNTRY>|<IBAN>", e.g.
"safepay/v1/payee|GB|GB82WEST12345698765432". The core intel client MUST build x with the same function.
"""

from __future__ import annotations

import re

from safepay_intel.policy import network_policy

_IBAN = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")


def normalise_iban(iban: str) -> str:
    return "".join(iban.split()).upper()


def iban_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check over the normalised IBAN."""
    if not _IBAN.match(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def payee_input(country: str, iban: str) -> bytes:
    norm = normalise_iban(iban)
    if not _COUNTRY.match(country) or not iban_valid(norm):
        raise ValueError("invalid payee identifier")
    return f"{network_policy().input_domain}|{country}|{norm}".encode()


def tag_of(output: bytes) -> str:
    """Snapshot tag: first tag_bytes bytes of the 64-byte OPRF output, lower-case hex."""
    return output[: network_policy().tag_bytes].hex()
