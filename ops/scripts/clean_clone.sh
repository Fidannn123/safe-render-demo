#!/usr/bin/env bash
# make clean-clone: what a reviewer does on a clean machine. A fresh copy of the project goes into a
# temporary directory (the committed HEAD, or the files git would not ignore when this folder is not its
# own repository), then the README Docker quickstart, the demo, the evidence run and every check run there
# on a separate compose project. All data is SYNTHETIC; the bank is SIMULATED.
#
#   bash ops/scripts/clean_clone.sh                     full run. Needs Docker, uv, make (pnpm or corepack
#                                                       for the console checks) and free ports 8000-8002,
#                                                       8080 and 55432: run `make down` first.
#   bash ops/scripts/clean_clone.sh --copy-only DIR     only write the fresh copy to DIR (no Docker needed)
#
# CLEAN_CLONE_KEEP=1 keeps the temporary copy. The copy builds the same image tags as the main stack
# (safepay/python:dev, safepay/console:dev), from the same code; its containers and volumes are its own.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

fresh_copy() {  # fresh_copy <new directory>
  local dest="$1" top
  top="$(git -C "$REPO" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ -z "$top" ]; then
    echo "clean-clone: git is required to tell project files from generated ones" >&2
    exit 2
  fi
  if [ "$(cd "$top" && pwd)" = "$REPO" ]; then
    git clone --quiet --no-hardlinks "$REPO" "$dest"
    echo "clean-clone: cloned HEAD $(git -C "$REPO" rev-parse --short HEAD); uncommitted changes are not included"
  else
    echo "clean-clone: $REPO is not its own git repository; copying the files git would not ignore"
    mkdir -p "$dest"
    (cd "$REPO" && git ls-files -z --cached --others --exclude-standard -- . | tar --null -T - -cf -) \
      | tar -xf - -C "$dest"
  fi
}

if [ "${1:-}" = "--copy-only" ]; then
  if [ -z "${2:-}" ] || [ -e "$2" ]; then
    echo "usage: $0 --copy-only NEW_DIRECTORY (it must not exist yet)" >&2
    exit 2
  fi
  fresh_copy "$2"
  echo "clean-clone: $(find "$2" -type f -not -path '*/.git/*' | wc -l | tr -d ' ') files in $2"
  exit 0
fi

for tool in docker uv make; do
  command -v "$tool" >/dev/null || { echo "clean-clone: needs $tool (it runs the README quickstart)" >&2; exit 2; }
done
for port in 8000 8001 8002 8080 55432; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then
    echo "clean-clone: port $port is in use; stop the running stack first (make down)" >&2
    exit 2
  fi
done

WORK="$(mktemp -d)"
# Own containers, network and volumes: never touches the user's stack or its database volume.
export COMPOSE_PROJECT_NAME=safepay-clean-clone
export SAFEPAY_RUNNER=docker

cleanup() {
  local status=$?
  if [ -f "$WORK/safepay/Makefile" ]; then
    (cd "$WORK/safepay" && make down-volumes >/dev/null 2>&1) || true
  fi
  if [ "${CLEAN_CLONE_KEEP:-0}" = 1 ]; then
    echo "clean-clone: kept $WORK/safepay"
  else
    rm -rf "$WORK"
  fi
  exit "$status"
}
trap cleanup EXIT

fresh_copy "$WORK/safepay"
cd "$WORK/safepay"

step() {
  echo
  echo "== clean-clone: $*"
  "$@"
}
step make up
step make seed
step make demo
step make evidence SEED=42
step make verify-evidence SEED=42
step make verify-audit
step make verify
echo
echo "clean-clone: OK (fresh copy, README quickstart, seed, demo, evidence, verify-evidence, verify)"
