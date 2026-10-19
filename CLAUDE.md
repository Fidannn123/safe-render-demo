# CLAUDE.md: SafePay

You are building **SafePay**, an entry for the FinTechathon 2026 International Track, **Topic E (SME Finance Copilot)**. Hard deadline: **20 Oct 2026, 19:59 Baku time (UTC+4)**. Internal v1 submit: **20 Oct 07:59 Baku**.

SafePay is a cash-flow-safe payables copilot: an AI agent that pays SME supplier invoices only after deterministic rules, a cash-buffer forecast and a privacy-preserving cross-business fraud network agree. It also raises cash-flow alerts, answers financial-analysis questions and matches financing options.

## Read before any work
1. `docs/architecture.md`: the design. It is authoritative. Do not redesign.
2. `docs/BUILD_PLAN.md`: order of work, gates, per-session prompts, default policy values.
3. `docs/COMPETITION.md`: scoring (40% task completion, 30% security, 30% innovation), the 5 measured tasks, what the demo must prove.
4. `spec/` (once it exists): frozen contracts. **Never edit `spec/`** after gate G0 unless the human explicitly says "unfreeze spec". If the spec looks wrong, stop and report the exact conflict.

## Non-negotiable decisions (override anything else you know or find)
- Backend: **Python 3.13 + Django 5.2 LTS** + **django-ninja** (pydantic schemas, OpenAPI 3.1). Not Node, not FastAPI, not DRF.
- Database: **PostgreSQL 18**, psycopg 3. One database and one DB role per service (core, intel, bank). RLS on tenant tables.
- Passwords: **argon2-cffi**, `django.contrib.auth.hashers.Argon2PasswordHasher` **first** in `PASSWORD_HASHERS`; PBKDF2 second only for upgrade.
- Async: **transactional outbox + worker** (`SELECT ... FOR UPDATE SKIP LOCKED`, Django management command). **No Celery, no Redis, no Django Tasks.**
- Frontend/tooling: **pnpm only** (`pnpm-lock.yaml`, `pnpm-workspace.yaml`). Never npm or yarn, never commit `package-lock.json`.
- Python deps: **uv** (`uv.lock`). Tools: ruff, mypy, pytest, hypothesis, schemathesis.
- **No smart contracts. No Solidity. No money movement or custody on any chain.** Blockchain exists only as the optional `AnchorPort` adapter (off by default; publishes an audit checkpoint hash via OpenTimestamps or a zero-value data-only tx via web3.py 8; no contract, no funds), labelled "requires independent audit before production". Build it last, or not at all.
- **No custom cryptography.** Only thin wrappers over: `cryptography` (pyca, Ed25519, AES-256-GCM, HMAC-SHA256, SHA-256), `voprf` 0.2 (RFC 9497, ristretto255-SHA512), `argon2-cffi`. Never implement curve arithmetic, hash-to-group, DLEQ proofs, or your own MAC/KDF constructions.
- Packaging: `docker compose` (core-web, core-worker, intel, bank, postgres, console static). One command on a clean machine. No cloud dependency for reviewers.

## Repository layout
```
spec/          openapi-core.yaml openapi-intel.yaml openapi-bank.yaml schemas/*.json policy.yaml
               state_machine.yaml ledger_accounts.yaml vectors/            # FROZEN at G0
core/          Django project safepay_core; apps: accounts tenants vendors invoices agent guardian
               payments ledger cashflow approvals audit intel_client bank_adapter recon evidence keystore
intel/         separate Django project + DB; apps: members oprf reports snapshots
bank_sandbox/  separate Django project + DB (simulated bank)
console/       Vite + React + TS (pnpm); packages/api-client/ generated from spec via openapi-typescript
sim/           twins scenarios attacker harness (Python)
evidence/      GENERATED ONLY (gitignored except schema/README)
ops/           docker-compose.yml, Makefile, .github/workflows/
docs/          architecture.md COMPETITION.md BUILD_PLAN.md KICKOFF_PROMPT.md SECURITY.md (later)
```
Module ownership (M1-M10) and their acceptance gates are in `docs/architecture.md` section 6. Stay inside the module you were assigned; touch other modules only through their `spec/` contract.

## Commands (create these at G0; keep them working)
- `make up` / `make down`: compose stack. `make migrate`, `make seed SEED=42`.
- `make verify`: ruff + mypy + pytest (unit, property, contract) + CFRG vectors + `pnpm -r lint test`. **Must be green before you say you are done.**
- `make demo`: scripted demo scenario. `make evidence SEED=42`: full B0/B1/B2 run into `evidence/run-42/`.
- `make verify-evidence`, `make verify-audit`, `make clean-clone` (fresh clone in a container, README steps, verify).

## Invariants that live in PostgreSQL (not only in Python)
- Journal entry postings sum to zero per currency: **deferred constraint trigger** at COMMIT.
- `CHECK (balance >= 0)` on `Bank:available`.
- Ledger and audit tables: INSERT-only for the app role (UPDATE/DELETE revoked + trigger raises).
- `UNIQUE(tenant, invoice_fingerprint)` over live payments; `UNIQUE(decision.nonce)`; `CHECK (approver_id <> initiator_id)`.
- Caps evaluated under `SELECT ... FOR UPDATE` on the tenant limits row; rolling 24 h window, never calendar day.
- Money: `BIGINT` minor units + ISO-4217 currency. Never floats. Demo currency USD.
- Every state transition = one transaction: state row + postings + audit event + outbox row.

## Security rules for code
- Payee account always comes from the **vendor registry**, never from invoice text.
- Extractor LLM: no tools, output validated by pydantic; free text stays opaque and tagged `untrusted`. Planner never sees raw invoice text; its only consequential tool is `propose_payment`.
- Every number the agent shows comes from a deterministic function; LLM only phrases.
- Default LLM is a **deterministic mock** (`SAFEPAY_LLM=mock`). Real provider via env: `SAFEPAY_LLM=anthropic`, `ANTHROPIC_API_KEY`, `SAFEPAY_LLM_MODEL` (pinned id). CI and tests always use the mock.
- Network signals add risk and can force HOLD; they **never DENY**. Intel unavailable: new payees HOLD (fail closed), known payees continue.
- Agent tokens: 256-bit random, stored as HMAC-SHA256(pepper, token). Human approve/unfreeze/registry edit require step-up OTP ≤5 min.
- Errors: RFC 9457 problem+json with typed codes from `spec/`.
- No secrets in git. Test keys generated at runtime into `ops/dev-keys/` (gitignored). `.env.example` only.
- No external fonts/CDNs in the console (reviewers may be in mainland China). Bundle assets.

## Definition of done (any task)
1. Code is typed; mypy **strict** for `ledger`, `payments`, `guardian`, `intel` and crypto wrappers.
2. Tests written first or alongside; acceptance gate for the module passes; `make verify` green.
3. Illegal state transitions each have a test that they raise.
4. No hand-typed metrics, hashes, latencies or counts in any doc; docs pull from `evidence/` outputs.
5. Synthetic data and simulated components are labelled as such in UI, README and docs.
6. Short summary of what changed + anything a human must review (always flag changes to M1, M2, crypto wrappers).

## Known gotchas
- `voprf` 0.2 (PyPI) wraps Meta's Rust crate; its Python API is not well documented. Inspect it (`python -c "import voprf; help(voprf)"`), wrap it behind `intel/apps/oprf/port.py` (`OprfServer`, `OprfClient`), and prove the wrapper against the CFRG RFC 9497 vectors (`spec/vectors/`, ristretto255-SHA512 VOPRF mode) before anything else uses it. If it cannot pass the vectors within 2 hours, stop and report; fallback is `pyoprf` (OPRF mode, non-verifiable, LGPLv3), which must be disclosed as a downgrade.
- Django's default isolation is READ COMMITTED; we rely on row locks, not SERIALIZABLE. If you add SERIALIZABLE anywhere, add retry on serialization failure.
- RLS: set `app.tenant_id` per request/transaction (`SET LOCAL`) in middleware; test that a query without it returns nothing.
- Deferred constraint triggers fire at COMMIT; Django tests using `TestCase` wrap in a transaction, so use `TransactionTestCase` (or explicit savepoint commit) for ledger invariant tests.
- django-ninja + Idempotency-Key: implement as a decorator/middleware storing request hash + response in the same transaction as the write.

## Working style
- Follow the gate order in BUILD_PLAN.md. At each gate, stop and report: what passes, what fails, what you cut.
- Prefer small, reviewable commits with conventional messages (`feat(guardian): ...`).
- When in doubt between more features and passing tests, choose passing tests. A small working network check beats a broken big one.
- Ask the human only for things only a human can provide (keys, accounts, scope decisions). Otherwise decide, record the decision in `docs/DECISIONS.md`, and continue.
