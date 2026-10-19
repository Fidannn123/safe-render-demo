"""Render SYNTHETIC invoices as RFC 5322 email (text/plain body) or text PDF. Deterministic bytes."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .iban import grouped
from .model import day_to_date, iso_day
from .pdf import PdfLine, render_pdf
from .textutil import money

BANNER = "SYNTHETIC TEST DATA - SafePay digital twin invoice. Not a real company or bank account."


@dataclass
class InvoiceDoc:
    vendor_name: str
    vendor_tax_id: str
    sender: str
    company: str
    reference: str
    issue_day: int
    due_day: int
    terms_days: int
    amount_minor: int
    lines: list[int]
    line_labels: list[str]
    currency: str = "USD"
    iban_printed: str | None = None
    iban_country: str | None = None
    discount_bp: int | None = None
    discount_days: int | None = None
    late_fee_minor: int = 0
    note: str | None = None
    hidden: list[str] = field(default_factory=list)


def body_lines(doc: InvoiceDoc) -> list[tuple[str, bool]]:
    """(text, hidden) lines shared by both renderings."""
    out: list[tuple[str, bool]] = [
        (BANNER, False),
        ("", False),
        ("INVOICE", False),
        (f"From: {doc.vendor_name}", False),
        (f"Tax ID: {doc.vendor_tax_id}", False),
        (f"Bill to: {doc.company}", False),
        (f"Invoice number: {doc.reference}", False),
        (f"Issue date: {iso_day(doc.issue_day)}", False),
        (f"Due date: {iso_day(doc.due_day)}", False),
        (f"Payment terms: Net {doc.terms_days} days", False),
        ("", False),
        ("Line items:", False),
    ]
    for label, amount in zip(doc.line_labels, doc.lines, strict=True):
        out.append((f"  - {label}: {money(amount, doc.currency)}", False))
    out.append((f"Amount due: {money(doc.amount_minor, doc.currency)}", False))
    if doc.discount_bp is not None and doc.discount_days is not None:
        pct = f"{doc.discount_bp // 100}.{doc.discount_bp % 100:02d}%"
        out.append((f"Early payment discount: {pct} if paid within {doc.discount_days} days", False))
    if doc.late_fee_minor:
        out.append((f"Late payment fee: {money(doc.late_fee_minor, doc.currency)}", False))
    out.append(("", False))
    if doc.iban_printed is not None:
        out.append((f"Bank details: IBAN {grouped(doc.iban_printed)} ({doc.iban_country})", False))
    else:
        out.append(("Bank details: as held on file (no change)", False))
    if doc.note:
        out.append(("", False))
        out.append((f"Notes: {doc.note}", False))
    out.append(("", False))
    out.append((f"Thank you for your business. {doc.vendor_name} accounts team.", False))
    for h in doc.hidden:
        out.append((h, True))
    return out


def render_email(doc: InvoiceDoc, message_id: str, recipient: str, sent_at: dt.datetime) -> bytes:
    lines = body_lines(doc)
    body: list[str] = []
    for text, hidden in lines:
        if hidden:
            body.extend([""] * 12)  # pushed below the visible fold of a mail client
        body.append(text)
    headers = [
        f"From: {doc.vendor_name} <{doc.sender}>",
        f"To: Accounts Payable <{recipient}>",
        f"Subject: Invoice {doc.reference} due {day_to_date(doc.due_day).isoformat()}",
        f"Date: {sent_at.strftime('%a, %d %b %Y %H:%M:%S +0000')}",
        f"Message-ID: <{message_id}>",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: 8bit",
        "X-SafePay-Synthetic: true",
    ]
    return ("\n".join(headers) + "\n\n" + "\n".join(body) + "\n").encode("utf-8")


def render_invoice_pdf(doc: InvoiceDoc) -> bytes:
    lines = [PdfLine(text, hidden=hidden, bold=(text == "INVOICE")) for text, hidden in body_lines(doc)]
    return render_pdf(lines, title=f"Invoice {doc.reference} (SYNTHETIC)")
