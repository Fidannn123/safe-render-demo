# SafePay make targets (included by the root Makefile). Run from the repository root.
# Requirements: Docker (compose v2), uv, Node 22 + corepack (pnpm). GNU make.

SHELL := bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

SEED ?= 42
UV ?= uv
PNPM ?= $(if $(shell command -v pnpm 2>/dev/null),pnpm,corepack pnpm)
COMPOSE ?= docker compose -f ops/docker-compose.yml
HARNESS := cd sim && $(UV) run --frozen python -m safepay_sim.harness
NATIVE := $(UV) run --frozen python ops/scripts/native_stack.py
# docker (the default when docker is installed) or native (ops/scripts/native_stack.py, no Docker)
export SAFEPAY_RUNNER ?= $(if $(shell command -v docker 2>/dev/null),docker,native)

# keygen writes ops/dev-keys as the invoking user on Linux/macOS (root is fine on Docker Desktop).
export SAFEPAY_UID ?= $(shell id -u 2>/dev/null || echo 0)
export SAFEPAY_GID ?= $(shell id -g 2>/dev/null || echo 0)

PY_PROJECTS := core intel bank_sandbox

.PHONY: spec-guard help up down down-volumes logs ps keys migrate seed db-up \
        verify lint typecheck test test-spec test-python test-console \
        demo hold evidence evidence-llm verify-evidence verify-audit tech-doc clean-clone fetch-vectors \
        native-init native-up native-down native-status native-reset

help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

# ------------------------------------------------------------------ stack
up: ## Build and start the whole stack, wait until every service is healthy
	$(COMPOSE) up -d --build --wait

down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

down-volumes: ## Stop the stack and DELETE the database volume (and the demo users that lived in it)
	$(COMPOSE) down -v
	rm -rf ops/dev-keys/demo

logs: ## Follow logs
	$(COMPOSE) logs -f --tail=100

ps: ## Service status
	$(COMPOSE) ps -a

keys: ## Generate dev secrets/keys into ops/dev-keys (idempotent, gitignored)
	$(UV) run --frozen python ops/scripts/gen_dev_keys.py ops/dev-keys

# In a worktree (SAFEPAY_TEST_DB_SUFFIX set) never touch compose: only the main checkout runs it.
db-up: ## Start only PostgreSQL (used by the test suite; no-op in worktrees)
ifneq ($(strip $(SAFEPAY_TEST_DB_SUFFIX)),)
	@echo "worktree $(SAFEPAY_TEST_DB_SUFFIX): using the shared PostgreSQL started by the main checkout"
else ifeq ($(SAFEPAY_RUNNER),native)
	@$(NATIVE) status
else
	$(COMPOSE) up -d --build --wait postgres
endif

migrate: ## Apply migrations as the owner roles (one-shot migrate service)
	$(COMPOSE) run --rm migrate

seed: ## Seed the 8 SYNTHETIC twins into the running stack: users, registry, 90-day history (SEED=42)
	$(HARNESS) seed --seed $(SEED)

# ------------------------------------------------------------------ verify
verify: spec-guard lint typecheck test ## spec frozen check + ruff + mypy + pytest (unit, property, contract, vectors) + pnpm lint/test
	@echo "make verify: OK"

spec-guard: ## Fail if spec/ differs from the spec-frozen tag (FROZEN after G0; "unfreeze spec" only)
	@if git rev-parse -q --verify refs/tags/spec-frozen >/dev/null; then 	  git diff --quiet spec-frozen -- spec && git diff --quiet --cached spec-frozen -- spec 	  || { echo "spec/ differs from tag spec-frozen: spec is FROZEN" >&2; git diff --stat spec-frozen -- spec >&2; exit 1; }; 	  echo "spec-guard: spec/ matches spec-frozen"; 	else echo "spec-guard: no spec-frozen tag yet"; fi

lint: ## ruff check + format check
	$(UV) run --frozen ruff check .
	$(UV) run --frozen ruff format --check .

typecheck: ## mypy per project (strict where CLAUDE.md requires)
	@for p in $(PY_PROJECTS) sim; do echo "mypy $$p"; (cd $$p && $(UV) run --frozen mypy .) || exit 1; done

test: test-spec test-python test-console ## All tests

test-spec: ## Spec contracts, schemas, OpenAPI lint, RFC vectors
	$(UV) run --frozen pytest tests

test-python: db-up ## Django projects (PostgreSQL 18) + sim
	@for p in $(PY_PROJECTS) sim; do echo "pytest $$p"; (cd $$p && $(UV) run --frozen pytest) || exit 1; done

test-console: ## pnpm: generate api-client, lint, test
	$(PNPM) install --frozen-lockfile
	$(PNPM) run generate
	$(PNPM) -r run lint
	$(PNPM) -r run test

# ------------------------------------------------------------------ harness and evidence (M10)
demo: ## Scripted walkthrough on the running, seeded stack (make up && make seed first)
	$(HARNESS) demo

hold: ## Leave one HELD payment in a twin's approvals queue for the console (TWIN=logistics)
	$(HARNESS) hold --twin $(or $(TWIN),logistics)

evidence: ## Full B0/B1/B2 run into evidence/run-$(SEED)/ (fresh database; bank + intel must be up)
	$(HARNESS) evidence --seed $(SEED)

evidence-llm: ## Real-LLM extractor measurement into evidence/llm-<provider>-$(SEED)/ (needs ANTHROPIC_API_KEY; PROVIDER=mock for the baseline)
	$(HARNESS) evidence-llm --seed $(SEED) --provider $(or $(PROVIDER),anthropic)

verify-evidence: ## Re-verify signatures and recompute metrics of evidence/run-$(SEED)/ (offline)
	$(HARNESS) verify-evidence --seed $(SEED)

verify-audit: ## Recompute every seeded tenant's audit hash chain from genesis
	$(HARNESS) verify-audit

tech-doc: ## Technical documentation PDF: docs/architecture.md + evidence/run-$(SEED) results (needs Chromium)
	$(UV) run --frozen python ops/scripts/build_tech_doc.py --seed $(SEED)

# ------------------------------------------------------------------ without Docker (dev machines)
native-init: ## No Docker: PostgreSQL 18 cluster (PG_BIN=<bin dir>), roles, databases, migrations
	$(NATIVE) init

native-up: ## No Docker: start postgres, bank, intel, core, worker and the console (:5173)
	$(NATIVE) start

native-down: ## No Docker: stop everything native-up started
	$(NATIVE) stop

native-status: ## No Docker: health of every service
	$(NATIVE) status

native-reset: ## No Docker: drop and recreate the three databases (wipes all data)
	$(NATIVE) reset

clean-clone: ## Fresh copy in a temp dir, README Docker quickstart, demo, evidence, verify (needs Docker)
	bash ops/scripts/clean_clone.sh

fetch-vectors: ## Re-download official test vectors (ONLY after "unfreeze spec")
	$(UV) run --frozen python -I ops/scripts/fetch_vectors.py .
