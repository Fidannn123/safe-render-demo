# ruff: noqa: E501  (SVG markup strings)
"""Build the README tech-stack badges from Simple Icons (CC0) SVGs. Offline, no CDN at view time.

  python docs/assets/stack/build_badges.py <path to an extracted simple-icons package>

Brand names and logos are trademarks of their owners; they only name the tools SafePay uses.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
# (file name, Simple Icons slug, label, value)
BADGES = [
    ("python", "python", "Python", "3.13"),
    ("django", "django", "Django", "5.2 LTS"),
    ("postgresql", "postgresql", "PostgreSQL", "18 + RLS"),
    ("react", "react", "React", "19"),
    ("typescript", "typescript", "TypeScript", "5.9"),
    ("vite", "vite", "Vite", "8"),
    ("pnpm", "pnpm", "pnpm", "10"),
    ("uv", "uv", "uv", "lockfile"),
    ("docker", "docker", "Docker", "compose"),
    ("nginx", "nginx", "nginx", "console"),
    ("pytest", "pytest", "pytest", "+ Hypothesis"),
    ("ruff", "ruff", "Ruff", "+ mypy strict"),
    ("githubactions", "githubactions", "GitHub Actions", "CI"),
    ("claude", "claude", "Claude", "optional LLM"),
]


def _width(text: str) -> int:
    return int(len(text) * 7.5) + 2


def badge(path_d: str, color: str, label: str, value: str) -> str:
    left = 30 + _width(label) + 10
    right = _width(value) + 20
    total = left + right
    dark = int(color[0:2], 16) * 299 + int(color[2:4], 16) * 587 + int(color[4:6], 16) * 114 > 150000
    ink = "#14202b" if dark else "#ffffff"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="28" role="img" aria-label="{label}: {value}">
<title>{label}: {value}</title>
<clipPath id="r"><rect width="{total}" height="28" rx="5"/></clipPath>
<g clip-path="url(#r)"><rect width="{left}" height="28" fill="#1f2a36"/><rect x="{left}" width="{right}" height="28" fill="#{color}"/></g>
<g transform="translate(9 6) scale(0.6667)" fill="#ffffff"><path d="{path_d}"/></g>
<g font-family="Verdana,DejaVu Sans,Geneva,sans-serif" font-size="12" font-weight="600">
<text x="30" y="18.5" fill="#ffffff">{label}</text>
<text x="{left + 10}" y="18.5" fill="{ink}">{value}</text></g>
</svg>
"""


def main() -> int:
    package = Path(sys.argv[1])
    data = {i["title"]: i for i in json.loads((package / "data" / "simple-icons.json").read_text())}
    by_slug = {re.sub(r"[^a-z0-9]", "", t.lower()): i for t, i in data.items()}
    for name, slug, label, value in BADGES:
        svg = (package / "icons" / f"{slug}.svg").read_text(encoding="utf-8")
        path_d = re.search(r' d="([^"]+)"', svg)
        if path_d is None:
            raise SystemExit(f"no path in {slug}.svg")
        (HERE / f"{name}.svg").write_text(
            badge(path_d[1], by_slug[slug]["hex"], label, value), encoding="utf-8"
        )
    print(f"wrote {len(BADGES)} badges to {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
