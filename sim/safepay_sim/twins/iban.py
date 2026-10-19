"""IBAN helpers for SYNTHETIC registry accounts (ISO 13616 mod-97). No external calls."""

from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

# BBAN layouts used by the twins: (letters prefix, digits). All produce valid ISO 13616 shapes.
#   DE: 18 digits; AT: 16 digits; GB: 4 letters + 14 digits; NL: 4 letters + 10 digits.
BBAN_LAYOUT: dict[str, tuple[int, int]] = {
    "DE": (0, 18),
    "AT": (0, 16),
    "GB": (4, 14),
    "NL": (4, 10),
}


def _to_digits(text: str) -> str:
    return "".join(str(int(ch, 36)) for ch in text)


def check_digits(country: str, bban: str) -> str:
    """ISO 13616 check digits for country + BBAN."""
    rearranged = bban + country + "00"
    return f"{98 - int(_to_digits(rearranged)) % 97:02d}"


def make_iban(country: str, bban: str) -> str:
    return f"{country}{check_digits(country, bban)}{bban}"


def normalise(iban: str) -> str:
    return iban.replace(" ", "").upper()


def is_valid(iban: str) -> bool:
    """Mod-97 check (ISO 13616): rearranged numeric value mod 97 == 1."""
    s = normalise(iban)
    if len(s) < 15 or not s[:2].isalpha() or not s[2:4].isdigit() or not s.isalnum():
        return False
    return int(_to_digits(s[4:] + s[:4])) % 97 == 1


def bban(iban: str) -> str:
    return normalise(iban)[4:]


def grouped(iban: str) -> str:
    """Print form: groups of four characters."""
    s = normalise(iban)
    return " ".join(s[i : i + 4] for i in range(0, len(s), 4))


@dataclass
class IbanFactory:
    """Issues mod-97 valid IBANs whose BBAN first-4 and last-4 characters are unique per output.

    Uniqueness keeps benign accounts from accidentally matching the LOOKALIKE_ACCOUNT detector
    (spec/policy.yaml detectors.lookalike.account_prefix_suffix_chars = 4). Look-alike attack
    accounts are issued deliberately with :meth:`lookalike_of`.
    """

    prefixes: set[str] = field(default_factory=set)
    suffixes: set[str] = field(default_factory=set)

    def _random_bban(self, rng: random.Random, country: str) -> str:
        letters, digits = BBAN_LAYOUT[country]
        head = "".join(rng.choice(string.ascii_uppercase) for _ in range(letters))
        first_digit = str(rng.randint(1, 9))
        tail = first_digit + "".join(rng.choice(string.digits) for _ in range(digits - 1))
        return head + tail

    def issue(self, rng: random.Random, country: str) -> str:
        for _ in range(10_000):
            b = self._random_bban(rng, country)
            if b[:4] in self.prefixes or b[-4:] in self.suffixes:
                continue
            self.prefixes.add(b[:4])
            self.suffixes.add(b[-4:])
            return make_iban(country, b)
        raise RuntimeError("IBAN space exhausted")  # pragma: no cover

    def lookalike_of(self, rng: random.Random, target_iban: str) -> str:
        """New account sharing the last 4 BBAN characters with ``target_iban`` (first 4 differ)."""
        country = normalise(target_iban)[:2]
        target = bban(target_iban)
        for _ in range(10_000):
            b = self._random_bban(rng, country)
            b = b[:-4] + target[-4:]
            if b[:4] in self.prefixes or b == target:
                continue
            self.prefixes.add(b[:4])
            return make_iban(country, b)
        raise RuntimeError("IBAN space exhausted")  # pragma: no cover
