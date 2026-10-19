"""Deterministic text helpers: money formatting, reference normalisation, edit distance, skeleton."""

from __future__ import annotations

import unicodedata
from decimal import ROUND_HALF_EVEN, Decimal
from fractions import Fraction

# Small confusable map covering the look-alike substitutions the twins generate (sim-side only; the
# Guardian uses a maintained TR39 library). Maps to a Latin "skeleton" character.
_CONFUSABLES = {
    "0": "o",
    "1": "l",
    "|": "l",
    "\u0430": "a",  # Cyrillic a
    "\u0435": "e",  # Cyrillic ie
    "\u043e": "o",  # Cyrillic o
    "\u0440": "p",  # Cyrillic er
    "\u0441": "c",  # Cyrillic es
    "\u0456": "i",  # Cyrillic byelorussian-ukrainian i
}


def money(amount_minor: int, currency: str = "USD") -> str:
    """'USD 1,234.56' from integer minor units (2 decimals)."""
    sign = "-" if amount_minor < 0 else ""
    major, minor = divmod(abs(amount_minor), 100)
    return f"{currency} {sign}{major:,}.{minor:02d}"


def normalise_ref(ref: str) -> str:
    """Invoice reference normalisation: upper-case alphanumerics only."""
    return "".join(ch for ch in ref.upper() if ch.isalnum())


def damerau_levenshtein(a: str, b: str) -> int:
    """Optimal string alignment distance (adjacent transpositions count 1)."""
    rows = len(a) + 1
    cols = len(b) + 1
    d = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        d[i][0] = i
    for j in range(cols):
        d[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


def skeleton(name: str) -> str:
    folded = unicodedata.normalize("NFKC", name).casefold()
    mapped = "".join(_CONFUSABLES.get(ch, ch) for ch in folded)
    return mapped.replace("rn", "m")


def names_lookalike(a: str, b: str, max_distance: int) -> bool:
    if a == b:
        return False
    sa, sb = skeleton(a), skeleton(b)
    return sa == sb or damerau_levenshtein(a.casefold(), b.casefold()) <= max_distance


def round_half_even(value: Fraction) -> int:
    return round(value)


def decimal_str(value: Fraction, places: int) -> str:
    """Exact rational -> fixed-point decimal string, ROUND_HALF_EVEN. Never via float."""
    q = Decimal(value.numerator) / Decimal(value.denominator)
    return str(q.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN))


def slug(text: str) -> str:
    out = []
    for ch in unicodedata.normalize("NFKD", text).lower():
        if ch.isascii() and ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")
