"""Deterministic detector primitives (pure functions, no I/O). Parameters come from spec/policy.yaml.

* confusable_skeleton: Unicode TR39-style skeleton built on the confusables data shipped by the
  maintained `confusable_homoglyphs` library (Unicode confusables.txt). The library stores, for each
  non-prototype character, a single-entry list holding its prototype; prototypes carry the list of
  their confusables. We map every character to its prototype (TR39: NFD, map, NFD) and casefold.
* damerau_levenshtein: unrestricted Damerau-Levenshtein distance (Lowrance-Wagner).
* robust_z_exceeds: |x - median| / (1.4826 * MAD) > threshold, exact Decimal arithmetic.
* injection_marker: instruction-like phrases in untrusted text (signal only).
"""

from __future__ import annotations

import functools
import re
import unicodedata
from collections.abc import Sequence
from decimal import Decimal

from confusable_homoglyphs import confusables

MAD_SCALE = Decimal("1.4826")
# Bidi marks wrapped around some confusables.txt targets (LRM, RLM, LRE..RLO).
_BIDI_MARKS: dict[int, None] = dict.fromkeys([0x200E, 0x200F, *range(0x202A, 0x202F)])
# Invisible / zero-width / bidi format characters stripped before matching (code points, not literals).
_INVISIBLE_RANGES = [
    (0x00AD, 0x00AD),
    (0x034F, 0x034F),
    (0x061C, 0x061C),
    (0x115F, 0x1160),
    (0x17B4, 0x17B5),
    (0x180E, 0x180E),
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2060, 0x206F),
    (0xFEFF, 0xFEFF),
]
_INVISIBLE = re.compile("[" + "".join(f"\\U{lo:08x}-\\U{hi:08x}" for lo, hi in _INVISIBLE_RANGES) + "]")


@functools.cache
def _prototype_map() -> dict[str, str]:
    data = confusables.confusables_data
    mapping: dict[str, str] = {}
    for char, homoglyphs in data.items():
        if len(char) != 1 or len(homoglyphs) != 1:
            continue
        target = str(homoglyphs[0]["c"]).translate(_BIDI_MARKS)
        if not target or target == char:
            continue
        back = data.get(target, [])
        # A 2-cycle (X->[Y], Y->[X]) has no distinguishable prototype: pick the smaller code point.
        if len(target) == 1 and len(back) == 1 and str(back[0]["c"]).translate(_BIDI_MARKS) == char:
            target = min(char, target)
            if target == char:
                continue
        mapping[char] = target
    return mapping


def confusable_skeleton(text: str) -> str:
    """TR39-style skeleton: NFD, invisible characters dropped, map to prototypes, NFD, casefold."""
    mapping = _prototype_map()
    nfd = unicodedata.normalize("NFD", _INVISIBLE.sub("", text))
    mapped = "".join(mapping.get(ch, ch) for ch in nfd)
    folded = unicodedata.normalize("NFD", mapped).casefold()
    # Second pass: casefolding can expose further confusables (e.g. capital forms); then drop marks
    # and collapse whitespace so "Acme  Ltd" and "Acme Ltd" compare equal.
    mapped = "".join(mapping.get(ch, ch) for ch in folded)
    no_marks = "".join(ch for ch in unicodedata.normalize("NFD", mapped) if not unicodedata.combining(ch))
    return " ".join(no_marks.split())


def damerau_levenshtein(a: str, b: str) -> int:
    """Unrestricted Damerau-Levenshtein distance (adjacent transpositions count 1)."""
    if a == b:
        return 0
    len_a, len_b = len(a), len(b)
    big = len_a + len_b
    last_row: dict[str, int] = {}
    d = [[0] * (len_b + 2) for _ in range(len_a + 2)]
    d[0][0] = big
    for i in range(len_a + 1):
        d[i + 1][0] = big
        d[i + 1][1] = i
    for j in range(len_b + 1):
        d[0][j + 1] = big
        d[1][j + 1] = j
    for i in range(1, len_a + 1):
        last_match_col = 0
        for j in range(1, len_b + 1):
            i1 = last_row.get(b[j - 1], 0)
            j1 = last_match_col
            cost = 1
            if a[i - 1] == b[j - 1]:
                cost = 0
                last_match_col = j
            d[i + 1][j + 1] = min(
                d[i][j] + cost,
                d[i + 1][j] + 1,
                d[i][j + 1] + 1,
                d[i1][j1] + (i - i1 - 1) + 1 + (j - j1 - 1),
            )
        last_row[a[i - 1]] = i
    return d[len_a + 1][len_b + 1]


def names_look_alike(a: str, b: str, max_distance: int) -> bool:
    """Same TR39 skeleton, or skeletons within max_distance Damerau-Levenshtein edits."""
    sa, sb = confusable_skeleton(a), confusable_skeleton(b)
    return sa == sb or damerau_levenshtein(sa, sb) <= max_distance


def bban(iban: str) -> str:
    """Basic bank account number: the IBAN without country code and check digits."""
    return iban[4:]


def accounts_look_alike(iban: str, other: str, chars: int) -> bool:
    """LOOKALIKE_ACCOUNT (D-015): different account sharing the first or last `chars` BBAN characters."""
    if iban == other:
        return False
    a, b = bban(iban), bban(other)
    if len(a) < chars or len(b) < chars:
        return False
    return a[:chars] == b[:chars] or a[-chars:] == b[-chars:]


def normalise_reference(reference: str) -> str:
    """Invoice reference for comparisons: NFKC, casefold, alphanumerics only."""
    return "".join(ch for ch in unicodedata.normalize("NFKC", reference).casefold() if ch.isalnum())


def median(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def robust_z_exceeds(x: int, history: Sequence[int], threshold: Decimal) -> bool:
    """|x - median| / (1.4826 * MAD) > threshold. MAD == 0: any deviation from the median exceeds."""
    values = [Decimal(v) for v in history]
    med = median(values)
    mad = median([abs(v - med) for v in values])
    deviation = abs(Decimal(x) - med)
    if mad == 0:
        return deviation > 0
    return deviation > threshold * MAD_SCALE * mad


_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instruction|instructions|prompt|rules|policy|above|previous|prior)\b",
        r"\b(system|developer)\s*(prompt|message|instruction)",
        r"\byou\s+are\s+(now|an?\s+(ai|assistant|agent|model))\b",
        r"\b(new|updated|revised)\s+instructions?\b",
        r"\b(as\s+an?\s+)?(ai|llm|language\s+model|assistant|agent)\b.{0,30}\b(must|should|will|shall)\b",
        r"\b(call|invoke|use|run)\s+(the\s+)?(tool|function)\b",
        r"\bpropose_payment\b|\brequest_human_approval\b|\bfunction_call\b|\btool_use\b",
        r"\b(do\s+not|don't|never)\s+(tell|inform|notify|alert|flag|mention|ask)\b",
        r"\b(approve|authori[sz]e|release)\s+(this|the)\s+(payment|transfer|invoice)\s+(immediately|now|without)\b",
        r"\b(skip|bypass|disable)\s+(the\s+)?(approval|review|check|checks|verification|guardian)\b",
        r"<\|?\s*(system|im_start|im_end|endoftext)\s*\|?>|\[/?inst\]|</?(system|assistant|user)>",
        r"(^|\n)\s*(system|assistant|user)\s*:",
    )
)


def injection_marker(text: str) -> bool:
    """True when the text contains instruction-like content aimed at an AI agent."""
    cleaned = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text)).casefold()
    return any(p.search(cleaned) for p in _INJECTION_PATTERNS)
