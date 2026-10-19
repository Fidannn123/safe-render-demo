"""invoice_fingerprint = SHA-256(vendor_id | normalised ref | amount_minor | currency).

Source: architecture.md section 2.5.
"""

from __future__ import annotations

import hashlib
import unicodedata
import uuid


def normalise_reference(reference: str | None) -> str:
    """NFKC, upper case, keep only Unicode letters and digits ("INV-001 " == "inv001")."""
    if not reference:
        return ""
    folded = unicodedata.normalize("NFKC", reference).upper()
    return "".join(ch for ch in folded if ch.isalnum())


def invoice_fingerprint(vendor_id: uuid.UUID, reference: str | None, amount_minor: int, currency: str) -> str:
    if type(amount_minor) is not int or amount_minor <= 0:
        raise ValueError("amount_minor must be a positive int")
    material = f"{vendor_id}|{normalise_reference(reference)}|{amount_minor}|{currency.upper()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
