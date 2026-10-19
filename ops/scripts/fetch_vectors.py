"""Fetch official test vectors into spec/vectors/ (run once at G0; output is committed).

Sources (pinned):
  * RFC 9497 (VOPRF): CFRG draft repo poc/vectors/allVectors.json at a pinned commit.
    This is the vector file the RFC 9497 Appendix A test vectors were generated from.
  * RFC 8785 (JCS): input/output pairs from the repository cited by RFC 8785 itself
    (cyberphone/json-canonicalization testdata), pinned commit.
  * RFC 8032 (Ed25519): section 7.1 test vectors, parsed from the RFC text on rfc-editor.org.

Every downloaded byte string is hashed into spec/vectors/SOURCES.sha256 so the freeze is auditable.
Usage: python -I ops/scripts/fetch_vectors.py <repo-root>
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

CFRG_COMMIT = "43da59f797c92248c0362e4c74cc10a8bd2b7b7d"
CFRG_URL = (
    f"https://raw.githubusercontent.com/cfrg/draft-irtf-cfrg-voprf/{CFRG_COMMIT}/poc/vectors/allVectors.json"
)
JCS_COMMIT = "32a48195b9b20fe67b1795e39e4509f14d860b45"
JCS_BASE = f"https://raw.githubusercontent.com/cyberphone/json-canonicalization/{JCS_COMMIT}/testdata"
JCS_CASES = ["arrays", "french", "structures", "unicode", "values", "weird"]
RFC8032_URL = "https://www.rfc-editor.org/rfc/rfc8032.txt"


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (fixed https URLs)
        data: bytes = resp.read()
    return data


def parse_rfc8032(text: str) -> list[dict[str, str]]:
    """Extract the Ed25519 vectors of RFC 8032 section 7.1 (TEST 1, 2, 3, 1024, SHA(abc))."""
    # Anchor on line starts: the table of contents repeats both headings (indented).
    start = re.search(r"^7\.1\.  Test Vectors for Ed25519$", text, re.M)
    end = re.search(r"^7\.2\.  Test Vectors for Ed25519ctx$", text, re.M)
    if start is None or end is None:
        raise ValueError("RFC 8032 section 7.1 not found")
    section = text[start.start() : end.start()]
    labels = {
        "SECRET KEY": "secret_key",
        "PUBLIC KEY": "public_key",
        "MESSAGE": "message",
        "SIGNATURE": "signature",
    }
    vectors: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    field: str | None = None
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if line.startswith("-----TEST "):
            current = {"name": line.removeprefix("-----"), **dict.fromkeys(labels.values(), "")}
            vectors.append(current)
            field = None
            continue
        if current is None:
            continue
        label = re.match(r"([A-Z][A-Z ]+?)(?: \(.*\))?:$", line)
        if label:
            field = labels.get(label.group(1))
        elif field and re.fullmatch(r"[0-9a-f]+", line):
            current[field] += line  # page headers/footers are non-hex and skipped
    for v in vectors:
        if len(v["public_key"]) != 64 or len(v["signature"]) != 128:
            raise ValueError(f"{v['name']}: malformed vector")
    return vectors


def main(root: Path) -> None:
    out = root / "spec" / "vectors"
    sums: list[str] = []

    raw = fetch(CFRG_URL)
    (out / "rfc9497").mkdir(parents=True, exist_ok=True)
    (out / "rfc9497" / "allVectors.json").write_bytes(raw)
    sums.append(f"{hashlib.sha256(raw).hexdigest()}  rfc9497/allVectors.json  {CFRG_URL}")

    for case in JCS_CASES:
        for kind in ("input", "output"):
            url = f"{JCS_BASE}/{kind}/{case}.json"
            data = fetch(url)
            dest = out / "rfc8785" / kind / f"{case}.json"
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
            sums.append(f"{hashlib.sha256(data).hexdigest()}  rfc8785/{kind}/{case}.json  {url}")

    rfc = fetch(RFC8032_URL)
    vectors = parse_rfc8032(rfc.decode("utf-8"))
    (out / "ed25519").mkdir(parents=True, exist_ok=True)
    doc = {
        "source": RFC8032_URL,
        "section": "7.1",
        "source_sha256": hashlib.sha256(rfc).hexdigest(),
        "vectors": vectors,
    }
    (out / "ed25519" / "rfc8032.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    sums.append(f"{hashlib.sha256(rfc).hexdigest()}  ed25519/rfc8032.json (parsed from)  {RFC8032_URL}")

    (out / "SOURCES.sha256").write_text("\n".join(sums) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(sums)} sources; {len(vectors)} Ed25519 vectors")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
