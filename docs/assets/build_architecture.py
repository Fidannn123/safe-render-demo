# ruff: noqa: E501  (SVG markup strings)
"""Build docs/assets/architecture.svg: the vertical data-flow diagram with tech-stack icons.

  python docs/assets/build_architecture.py <path to an extracted simple-icons package>

Icons from Simple Icons (CC0); trademarks belong to their owners. Offline: the SVG embeds every path.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "architecture.svg"
W, H = 960, 1250
CX, CW = 310, 340  # centre column x, width
LX, RX, SW = 20, 710, 230  # side columns x, width
FONT = "Segoe UI,Helvetica Neue,Arial,sans-serif"
DOC = (
    "M6 2h8l6 6v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2zm7 1.5V9h5.5z"
    "M7 12h10v1.6H7zm0 3.5h10v1.6H7zm0 3.5h6v1.6H7z"
)


class Icons:
    def __init__(self, package: Path) -> None:
        self.package = package
        meta = json.loads((package / "data" / "simple-icons.json").read_text(encoding="utf-8"))
        self.hex = {re.sub(r"[^a-z0-9]", "", i["title"].lower()): i["hex"] for i in meta}

    def path(self, slug: str) -> str:
        svg = (self.package / "icons" / f"{slug}.svg").read_text(encoding="utf-8")
        match = re.search(r' d="([^"]+)"', svg)
        if match is None:
            raise SystemExit(f"no path in {slug}.svg")
        return match[1]


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(  # noqa: PLR0917 (one call per box, read top to bottom)
    icons: Icons, x: int, y: int, w: int, h: int, slug: str | None, title: str, lines: list[str], accent: str
) -> str:
    color = {"claude": "D97757", "react": "087EA4"}.get(slug or "", icons.hex.get(slug or "", "5B6B7A"))
    d = icons.path(slug) if slug else DOC
    iy = y + (h - 30) / 2
    out = [
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="#ffffff" stroke="#{accent}" '
        f'stroke-width="1.6"/>',
        f'<rect x="{x}" y="{y}" width="6" height="{h}" rx="3" fill="#{accent}"/>',
        f'<g transform="translate({x + 18} {iy}) scale(1.25)" fill="#{color}"><path d="{d}"/></g>',
        f'<text x="{x + 62}" y="{y + 26}" font-size="15.5" font-weight="700" fill="#14202b">{esc(title)}</text>',
    ]
    for i, line in enumerate(lines):
        out.append(
            f'<text x="{x + 62}" y="{y + 46 + 17 * i}" font-size="12.5" fill="#4a5a69">{esc(line)}</text>'
        )
    return "\n".join(out)


def pill(x: int, y: int, w: int, text: str, fill: str) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{w}" height="40" rx="20" fill="#{fill}"/>'
        f'<text x="{x + w / 2}" y="{y + 25}" font-size="13.5" font-weight="700" fill="#ffffff" '
        f'text-anchor="middle">{esc(text)}</text>'
    )


def arrow(points: list[tuple[float, float]], label: str = "", at: tuple[float, float] | None = None) -> str:
    d = "M" + " L".join(f"{px} {py}" for px, py in points)
    out = f'<path d="{d}" fill="none" stroke="#5b6b7a" stroke-width="1.6" marker-end="url(#head)"/>'
    if label and at:
        width = len(label) * 6.3 + 10
        out += (
            f'<rect x="{at[0] - width / 2}" y="{at[1] - 11}" width="{width}" height="17" rx="4" fill="#eef3f7"/>'
            f'<text x="{at[0]}" y="{at[1] + 2}" font-size="11.5" fill="#33414e" text-anchor="middle">'
            f"{esc(label)}</text>"
        )
    return out


def build(icons: Icons) -> str:
    mid = CX + CW / 2
    g = []
    g.append(
        '<text x="24" y="36" font-size="19" font-weight="800" fill="#0f3d3e">SafePay: how a payment flows</text>'
    )
    g.append(
        '<text x="24" y="56" font-size="12.5" fill="#5b6b7a">The LLMs sit outside the authority path. '
        "Every number comes from deterministic code; PostgreSQL refuses anything that breaks the books.</text>"
    )
    # centre column
    g.append(
        card(icons, CX, 80, CW, 66, None, "Supplier invoice", ["email or text PDF, untrusted data"], "9AA5B1")
    )
    g.append(arrow([(mid, 146), (mid, 182)]))
    g.append(
        card(
            icons,
            CX,
            184,
            CW,
            74,
            "claude",
            "Extractor LLM",
            ["no tools, strict JSON schema out", "mock by default, Claude optional"],
            "D97757",
        )
    )
    g.append(arrow([(mid, 258), (mid, 296)], "typed fields only", (mid + 2, 279)))
    g.append(
        card(
            icons,
            CX,
            298,
            CW,
            74,
            "python",
            "Planner",
            ["one tool: propose_payment", "payee from the vendor registry"],
            "3776AB",
        )
    )
    g.append(arrow([(mid, 372), (mid, 410)]))
    g.append(
        card(
            icons,
            CX,
            412,
            CW,
            100,
            "django",
            "Guardian",
            [
                "hard rules, caps under a row lock",
                "13-week cash forecast, risk score",
                "signs an Ed25519 Decision",
            ],
            "0F3D3E",
        )
    )
    # fraud network (right) and DENY (left)
    g.append(
        card(
            icons,
            RX,
            412,
            SW,
            100,
            "django",
            "Fraud network",
            ["VOPRF, RFC 9497", "bank-attested reports", "never sees the payee"],
            "092E20",
        )
    )
    g.append(arrow([(CX + CW, 446), (RX - 2, 446)], "blinded", (CX + CW + 30, 438)))
    g.append(arrow([(RX, 482), (CX + CW + 2, 482)], "proof", (CX + CW + 30, 474)))
    g.append(pill(LX + 20, 442, 190, "DENY: stopped", "B42318"))
    g.append(arrow([(CX, 462), (LX + 212, 462)]))
    # row: approval, ledger, cash-flow copilot
    g.append(
        card(
            icons,
            LX,
            594,
            SW,
            84,
            "react",
            "Human approval",
            ["console, password + TOTP", "four eyes, reason required"],
            "087EA4",
        )
    )
    g.append(
        arrow(
            [(CX + 40, 512), (LX + SW / 2, 512 + 30), (LX + SW / 2, 592)], "HOLD T3", (LX + SW / 2 + 10, 560)
        )
    )
    g.append(arrow([(mid, 512), (mid, 592)], "ALLOW T1 / GUARDED T2", (mid + 4, 552)))
    g.append(
        card(
            icons,
            CX,
            594,
            CW,
            84,
            "postgresql",
            "Ledger and audit",
            ["double entry, RLS per tenant", "append-only, SHA-256 hash chain"],
            "4169E1",
        )
    )
    g.append(arrow([(LX + SW, 636), (CX - 2, 636)]))
    g.append(
        card(
            icons,
            RX,
            594,
            SW,
            84,
            "python",
            "Cash-flow copilot",
            ["13-week forecast, alerts", "DPO, DSO, financing match"],
            "3776AB",
        )
    )
    g.append(arrow([(CX + CW, 616), (RX - 2, 616)]))
    # outbox, bank, reconciler
    g.append(arrow([(mid, 678), (mid, 718)], "same transaction", (mid + 2, 698)))
    g.append(
        card(
            icons, CX, 720, CW, 70, "python", "Outbox worker", ["SKIP LOCKED, retries, idempotent"], "3776AB"
        )
    )
    g.append(arrow([(mid, 790), (mid, 830)], "transfer", (mid + 2, 810)))
    g.append(
        card(icons, CX, 832, CW, 70, "django", "Bank", ["simulated, signs receipts and statements"], "7A5AF8")
    )
    g.append(arrow([(mid, 902), (mid, 942)], "Ed25519-signed receipt", (mid + 2, 922)))
    g.append(
        card(icons, CX, 944, CW, 70, "python", "Reconciler", ["receipts vs ledger vs statement"], "3776AB")
    )
    g.append(arrow([(CX + CW, 990), (CX + CW + 30, 990), (CX + CW + 30, 660), (CX + CW + 2, 660)]))
    g.append(card(icons, RX, 944, SW, 70, "django", "Velocity monitor", ["refused agent requests"], "B42318"))
    # freeze
    g.append(pill(mid - 150, 1086, 300, "T4 freeze: outflow stops, tokens revoked", "7A271A"))
    g.append(arrow([(mid, 1014), (mid, 1084)], "bank mismatch", (mid + 2, 1050)))
    g.append(
        arrow(
            [(RX + SW / 2, 1014), (RX + SW / 2, 1106), (mid + 152, 1106)],
            "3rd refusal in 10 min",
            (RX + SW / 2, 1050),
        )
    )
    # stack strip
    strip = [
        ("docker", "Docker compose"),
        ("nginx", "nginx"),
        ("postgresql", "PostgreSQL 18"),
        ("django", "Django 5.2"),
        ("react", "React 19"),
    ]
    x = 24
    g.append(
        f'<text x="{x}" y="1172" font-size="12.5" font-weight="700" fill="#33414e">Runs locally with one command:</text>'
    )
    x = 230
    for slug, label in strip:
        color = {"react": "087EA4"}.get(slug, icons.hex[slug])
        g.append(
            f'<g transform="translate({x} 1157) scale(0.8333)" fill="#{color}"><path d="{icons.path(slug)}"/></g>'
        )
        g.append(f'<text x="{x + 26}" y="1172" font-size="12.5" fill="#33414e">{label}</text>')
        x += 26 + len(label) * 7 + 22
    g.append(
        '<text x="24" y="1210" font-size="12" fill="#6b7785">Tiers: T1 auto, T2 cool-off with owner veto, '
        "T3 human, T4 freeze. All data is SYNTHETIC and the bank is SIMULATED.</text>"
    )
    body = "\n".join(g)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img"
 aria-label="SafePay data flow" font-family="{FONT}">
<title>SafePay: how a payment flows</title>
<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
<path d="M0 0 L10 5 L0 10 z" fill="#5b6b7a"/></marker></defs>
<rect width="{W}" height="{H}" rx="16" fill="#f6f9fb"/>
{body}
</svg>
"""


def main() -> int:
    OUT.write_text(build(Icons(Path(sys.argv[1]))), encoding="utf-8")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
