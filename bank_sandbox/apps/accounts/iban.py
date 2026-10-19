"""IBAN mod-97 check (ISO 13616). The spec pattern checks shape only; validity is checked here."""

from __future__ import annotations

import re

IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$")


def iban_valid(iban: str) -> bool:
    if not IBAN_RE.match(iban):
        return False
    rearranged = iban[4:] + iban[:4]
    digits = "".join(str(int(ch, 36)) for ch in rearranged)
    return int(digits) % 97 == 1


def make_iban(country: str, bban: str) -> str:
    """Build a valid IBAN from a country code and BBAN (synthetic account seeding)."""
    digits = "".join(str(int(ch, 36)) for ch in bban + country + "00")
    check = 98 - int(digits) % 97
    return f"{country}{check:02d}{bban}"
