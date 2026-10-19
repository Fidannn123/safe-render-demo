"""CLI: python -m safepay_sim.twins --seed 42 --out <dir>  (writes SYNTHETIC twin files)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .generate import build


def write_tree(files: dict[str, bytes], out: Path) -> None:
    """Write files byte-for-byte (LF newlines preserved: binary mode)."""
    for rel, data in sorted(files.items()):
        path = out / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m safepay_sim.twins", description=__doc__)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.seed < 0:
        parser.error("--seed must be >= 0")
    out: Path = args.out
    if out.exists() and any(out.iterdir()):
        parser.error(f"--out {out} must be empty or absent (output is a pure function of the seed)")
    files = build(args.seed)
    write_tree(files, out)
    sys.stdout.write(f"wrote {len(files)} SYNTHETIC files for seed {args.seed} to {out}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
