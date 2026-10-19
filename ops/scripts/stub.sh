#!/usr/bin/env bash
# Placeholder for make targets that later phases implement. Exits non-zero on purpose so that a stub
# can never be mistaken for a passing step (no fake "green").
set -euo pipefail
target="$1"; owner="$2"; shift 2
echo "make ${target}: NOT IMPLEMENTED YET (owner: ${owner}) args: $*" >&2
exit 3
