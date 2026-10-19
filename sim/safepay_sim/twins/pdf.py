"""Minimal deterministic text-PDF writer (PDF 1.4, one font, no timestamps, no /ID).

Produces a real text layer (Helvetica, WinAnsi) so extraction does not need OCR. Lines marked hidden
are drawn in white: invisible on screen but present in the text layer (hidden-instruction attacks).
"""

from __future__ import annotations

from dataclasses import dataclass

PAGE_W = 612
PAGE_H = 792
TOP = 756
LEADING = 13
BOTTOM = 40
LINES_PER_PAGE = (TOP - BOTTOM) // LEADING


@dataclass(frozen=True)
class PdfLine:
    text: str
    hidden: bool = False
    bold: bool = False


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    out = bytearray()
    for byte in raw:
        if byte in (0x28, 0x29, 0x5C):  # ( ) backslash
            out += b"\\" + bytes([byte])
        elif byte < 0x20 or byte > 0x7E:
            out += f"\\{byte:03o}".encode("ascii")
        else:
            out.append(byte)
    return bytes(out)


def _page_stream(lines: list[PdfLine]) -> bytes:
    parts: list[bytes] = [b"BT", b"/F1 10 Tf", f"{LEADING} TL".encode(), f"50 {TOP} Td".encode()]
    for i, line in enumerate(lines):
        font = b"/F2 10 Tf" if line.bold else b"/F1 10 Tf"
        colour = b"1 1 1 rg" if line.hidden else b"0 0 0 rg"
        if i:
            parts.append(b"T*")
        parts += [font, colour, b"(" + _escape(line.text) + b") Tj"]
    parts.append(b"ET")
    return b"\n".join(parts) + b"\n"


def render_pdf(lines: list[PdfLine], title: str) -> bytes:
    pages = [lines[i : i + LINES_PER_PAGE] for i in range(0, max(len(lines), 1), LINES_PER_PAGE)]
    n_pages = len(pages)
    # Object numbers: 1 catalog, 2 pages, 3 font, 4 bold font, 5 info, then (page, content) pairs.
    objects: dict[int, bytes] = {}
    kids = []
    for idx, page_lines in enumerate(pages):
        page_no = 6 + 2 * idx
        content_no = page_no + 1
        kids.append(f"{page_no} 0 R")
        stream = _page_stream(page_lines)
        objects[page_no] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
            f"/Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents {content_no} 0 R >>"
        ).encode()
        objects[content_no] = f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream"
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {n_pages} >>".encode()
    objects[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    objects[4] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    objects[5] = b"<< /Title (" + _escape(title) + b") /Producer (safepay-sim twins SYNTHETIC) >>"

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in sorted(objects):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objects[num] + b"\nendobj\n"
    xref_at = len(out)
    size = max(objects) + 1
    out += f"xref\n0 {size}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, size):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {size} /Root 1 0 R /Info 5 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    return bytes(out)
