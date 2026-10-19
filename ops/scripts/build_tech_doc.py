"""Technical documentation PDF (deliverable 01): docs/architecture.md plus the generated results of a run.

    uv run --frozen python ops/scripts/build_tech_doc.py --seed 42

Writes evidence/run-<seed>/SafePay-Technical-Documentation.{html,pdf}. Every measured number comes from
that run's EVIDENCE.md (written by `make evidence`); none is typed here. Mermaid diagrams render offline
from node_modules/mermaid (pnpm), then headless Chromium prints the page. Set CHROME to the browser binary
if it is not found. SYNTHETIC data, SIMULATED bank.
"""

from __future__ import annotations

import argparse
import html
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt

REPO = Path(__file__).resolve().parents[2]
MERMAID = REPO / "node_modules" / "mermaid" / "dist" / "mermaid.min.js"
# Sections of EVIDENCE.md copied into the document, in this order.
EVIDENCE_SECTIONS = (
    "Results",
    "Agent tasks",
    "Attacks that still moved money",
    "Fraud network",
    "Verification",
)

DIFFERENCES = """
## 8. Where the prototype differs from this design

This document is the design. The prototype implements it with these differences, also listed in
`docs/SECURITY.md` (security self-assessment) and `docs/DECISIONS.md`:

- Encrypted account data is bound to its tenant (associated data = tenant id), not to its table, column
  and row as section 3.3 describes. Each tenant has its own data key wrapped by the KEK.
- The deterministic mock LLM backs every test and this evidence run. The real provider path is built
  and tested with a stubbed client, and `make evidence-llm` measures a real model when a key is set.
- T2 payments in the evidence run execute after the cool-off because the harness never vetoes. The one
  attack that still moves money this way is the first payment of a split to a new payee.
- Keys, including the KEK, are files on the same host as the data; the KeyStore port is built to map to
  a KMS or HSM.
"""

CSS = """
@page { size: A4; margin: 16mm 14mm 16mm 14mm; }
body { font-family: "Segoe UI", "Helvetica Neue", Arial, "Noto Sans", sans-serif; color: #1b2733;
       font-size: 10pt; line-height: 1.45; background: #fff; }
h1 { font-size: 20pt; margin: 0 0 4pt; color: #0f3d3e; }
h2 { font-size: 14pt; margin: 18pt 0 6pt; color: #0f3d3e; border-bottom: 1px solid #c9d6d6;
     padding-bottom: 2pt; break-after: avoid; }
h3 { font-size: 11.5pt; margin: 12pt 0 4pt; break-after: avoid; }
p, li { orphans: 3; widows: 3; }
table { border-collapse: collapse; width: 100%; margin: 6pt 0 10pt; font-size: 8.4pt; }
th, td { border: 1px solid #c9d6d6; padding: 3pt 5pt; vertical-align: top; text-align: left; }
th { background: #eef4f4; }
tr { break-inside: avoid; }
code { font-family: Consolas, "DejaVu Sans Mono", monospace; font-size: 8.6pt; background: #f2f5f5;
       padding: 0 2pt; border-radius: 2pt; }
pre { background: #f2f5f5; padding: 6pt 8pt; font-size: 8pt; line-height: 1.35; white-space: pre-wrap;
      break-inside: avoid; border-radius: 3pt; }
pre code { background: none; padding: 0; }
pre.mermaid { background: #fff; text-align: center; }
pre.mermaid svg { max-width: 100%; height: auto; max-height: 200mm; }
blockquote { margin: 6pt 0; padding: 4pt 10pt; border-left: 3pt solid #c58a1b; background: #fff8e8; }
.banner { border: 1px solid #c58a1b; background: #fff8e8; padding: 6pt 10pt; margin: 8pt 0 12pt;
          font-weight: 600; }
.meta { color: #51606b; margin: 0 0 6pt; }
"""


def architecture_body() -> str:
    """architecture.md without the internal competition notes, section 0 and the work schedule."""
    text = (REPO / "docs" / "architecture.md").read_text(encoding="utf-8")
    title, _, rest = text.partition("\n")
    rest = re.sub(r"\n> \*\*Verdict:.*?\n---\n", "\n", rest, count=1, flags=re.S)
    rest = re.sub(r"\n## 0\. .*?\n---\n", "\n", rest, count=1, flags=re.S)
    rest = re.sub(r"\n\| Window \(Baku time.*?\n\n", "\n\n", rest, count=1, flags=re.S)
    return title.removeprefix("# ") + "\n" + rest


def evidence_sections(seed: int) -> str:
    path = REPO / "evidence" / f"run-{seed}" / "EVIDENCE.md"
    if not path.exists():
        raise SystemExit(f"{path} not found: run `make evidence SEED={seed}` first")
    text = path.read_text(encoding="utf-8")
    sections = {m[1]: m[2].strip() for m in re.finditer(r"^## (.+?)\n(.*?)(?=^## |\Z)", text, re.M | re.S)}
    header = text.split("\n## ", 1)[0].splitlines()
    run_line = next((line for line in header if line.startswith("Run started")), "")
    parts = [
        f"## 9. Measured results (run-{seed})",
        "Copied from the generated `evidence/run-"
        f"{seed}/EVIDENCE.md`. SYNTHETIC data, SIMULATED bank, deterministic mock LLM. B0 is an undefended "
        "agent with direct transfer rights, B1 adds the Guardian and permission tiers, B2 adds the fraud "
        f"network. Re-check offline with `make verify-evidence SEED={seed}`.",
        run_line,
    ]
    for name in EVIDENCE_SECTIONS:
        if name in sections:
            parts += [f"### {name}", sections[name]]
    return "\n\n".join(parts)


def render_html(seed: int) -> str:
    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    default_fence = md.renderer.rules["fence"]

    def fence(tokens, idx, options, env):  # type: ignore[no-untyped-def]
        token = tokens[idx]
        if token.info.strip() == "mermaid":
            return f'<pre class="mermaid">{html.escape(token.content)}</pre>\n'
        return str(default_fence(tokens, idx, options, env))

    md.renderer.rules["fence"] = fence
    title, _, body = architecture_body().partition("\n")
    source = body + "\n" + DIFFERENCES + "\n" + evidence_sections(seed)
    mermaid_js = MERMAID.read_text(encoding="utf-8").replace("</script", "<\\/script")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>SafePay Technical Documentation</title>
<style>{CSS}</style></head><body>
<h1>{html.escape(title)}</h1>
<p class="meta">Technical documentation (deliverable 01). Generated by <code>make tech-doc SEED={seed}</code>
from <code>docs/architecture.md</code> and <code>evidence/run-{seed}/EVIDENCE.md</code>.</p>
<div class="banner">Prototype: all data is SYNTHETIC and the bank is SIMULATED. No real money moves.</div>
{md.render(source)}
<script>{mermaid_js}</script>
<script>
mermaid.initialize({{ startOnLoad: false, theme: "neutral", securityLevel: "strict",
  flowchart: {{ htmlLabels: false }}, fontFamily: "Arial, sans-serif" }});
mermaid.run({{ querySelector: "pre.mermaid" }})
  .then(() => {{ document.title = "ready"; }})
  .catch((error) => {{ document.title = "mermaid-error"; console.error(error); }});
</script>
</body></html>
"""


def chrome() -> str:
    candidates = [
        os.environ.get("CHROME", ""),
        "/opt/pw-browsers/chromium",
        *(shutil.which(name) or "" for name in ("chromium", "chromium-browser", "google-chrome", "chrome")),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for candidate in candidates:
        path = Path(candidate) if candidate else None
        if path and path.is_dir():
            found = sorted(path.glob("chromium-*/chrome-linux/chrome"))
            path = found[-1] if found else None
        if path and path.is_file():
            return str(path)
    raise SystemExit("no Chromium, Chrome or Edge found; set CHROME to the browser binary")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if not MERMAID.exists():
        raise SystemExit(f"{MERMAID} not found: run `pnpm install --frozen-lockfile`")
    out_dir = REPO / "evidence" / f"run-{args.seed}"
    page = out_dir / "SafePay-Technical-Documentation.html"
    pdf = page.with_suffix(".pdf")
    page.write_text(render_html(args.seed), encoding="utf-8")
    pdf.unlink(missing_ok=True)
    subprocess.run(  # noqa: S603 (fixed argv)
        [
            chrome(),
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            "--no-pdf-header-footer",
            "--disable-background-networking",
            "--no-first-run",
            "--virtual-time-budget=20000",
            f"--print-to-pdf={pdf}",
            page.as_uri(),
        ],
        check=True,
        capture_output=True,
    )
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise SystemExit("Chromium produced no PDF")
    print(f"wrote {pdf.relative_to(REPO)} and {page.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
