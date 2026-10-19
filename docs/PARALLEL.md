# PARALLEL.md: Phase 1 in parallel worktrees (M1-M5)

Gate **G1 by 17:30 Baku** (D-031). One git worktree and branch per module, all branched from `main` at
`spec-frozen` + 1. Only the **main checkout** runs docker compose; worktrees run tests against the shared
PostgreSQL with their own test databases (`test_safepay_<svc>_<suffix>`, D-034). The lead merges
`m1..m5` into `main` at the gate.

| Worktree | Branch | Module | Suffix |
|---|---|---|---|
| `C:\Users\Asus\safepay-wt\m1` | `m1` | M1 ledger + payments (+ audit event write, outbox, idempotency store) | `m1` |
| `C:\Users\Asus\safepay-wt\m2` | `m2` | M2 guardian (+ keystore) | `m2` |
| `C:\Users\Asus\safepay-wt\m3` | `m3` | M3 bank sandbox | `m3` |
| `C:\Users\Asus\safepay-wt\m4` | `m4` | M4 intel (VOPRF port, snapshots, reports) | `m4` |
| `C:\Users\Asus\safepay-wt\m5` | `m5` | M5 sim twins | `m5` |

## 1. One-time setup (main checkout, already done at 12:10 Baku)

```bash
cd C:/Users/Asus/OneDrive/Desktop/safepay
make up                                   # shared PostgreSQL + stack; keep it running
mkdir -p C:/Users/Asus/safepay-wt
for m in m1 m2 m3 m4 m5; do git worktree add -b $m C:/Users/Asus/safepay-wt/$m main; done
for m in m1 m2 m3 m4 m5; do (cd C:/Users/Asus/safepay-wt/$m && uv sync --all-packages --frozen && pnpm install --frozen-lockfile); done
```

## 2. Per-worktree shell (Git Bash; replace m1 with the module)

```bash
cd C:/Users/Asus/safepay-wt/m1
export SAFEPAY_TEST_DB_SUFFIX=m1
export SAFEPAY_KEYS_DIR=C:/Users/Asus/OneDrive/Desktop/safepay/ops/dev-keys
export PATH="$PATH:/c/Users/Asus/AppData/Local/Microsoft/WinGet/Packages/ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe/bin"
make verify                               # never: make up / down / keys / migrate, docker compose
```

PowerShell equivalent:

```powershell
cd C:\Users\Asus\safepay-wt\m1
$env:SAFEPAY_TEST_DB_SUFFIX = "m1"
$env:SAFEPAY_KEYS_DIR = "C:\Users\Asus\OneDrive\Desktop\safepay\ops\dev-keys"
make verify
```

Then start Claude Code in that directory (`claude`) and paste the session prompt from section 4.

## 3. Rules for every session

- spec/ is FROZEN (tag `spec-frozen`; `make verify` runs `spec-guard`). Never edit it. If it looks wrong,
  stop and report the exact conflict.
- Stay inside your module's directories. Shared files you may touch: your own `pyproject.toml` member
  (then `uv lock`), your project's `settings/base.py` only for settings your module owns. The lead
  resolves `uv.lock` conflicts at merge by re-locking.
- Append-only table names are a contract (D-033); use exactly these `db_table` names:
  core `ledger_journal_entry`, `ledger_posting`, `audit_event`, `audit_checkpoint`, `guardian_decision`,
  `bank_adapter_receipt`; intel `snapshots_snapshot`, `reports_receipt`; bank `transfers_receipt`,
  `statements_statement`.
- Any table with a `tenant_id` column automatically gets FORCE RLS (db_policy.py). Tests must set
  `app.tenant_id` (`SELECT set_config('app.tenant_id', <uuid>, true)` inside the transaction).
- Tests run as the owner role; prove app-role restrictions with a separate app-role connection
  (pattern: `core/tests/test_db_roles.py`). Ledger invariant tests use `TransactionTestCase` /
  `django_db(transaction=True)` (deferred triggers fire at COMMIT).
- Write tests first. `make verify` must be green. Small conventional commits on your branch. Do not merge,
  do not push, do not run docker compose.
- Finish with: what you built, test results, spec conflicts, and the lines a human must review
  (always M1, M2 and crypto wrappers).

## 4. Session prompts

### Common header (prepend to each prompt)

```
Read CLAUDE.md, docs/architecture.md, docs/BUILD_PLAN.md, docs/DECISIONS.md, docs/PARALLEL.md and spec/.
You own module <MODULE> only (Phase 1, session <S> in BUILD_PLAN.md). You work ONLY in the git worktree
<PATH> on branch <BRANCH>. In every shell: export SAFEPAY_TEST_DB_SUFFIX=<SUFFIX>
SAFEPAY_KEYS_DIR=C:/Users/Asus/OneDrive/Desktop/safepay/ops/dev-keys and put GNU make on PATH (PARALLEL.md
section 2). Never run docker compose, make up/down/keys/migrate. spec/ is FROZEN: never edit it; report
conflicts. Follow PARALLEL.md section 3. Write tests first; run `make verify` until green; commit small
conventional commits on <BRANCH>; do not merge or push. Deadline: G1 at 17:30 Baku.
Finish with: what you built, test results, spec conflicts, lines a human must review.
```

### M1 (session A): ledger + payments

```
Build M1 in core/apps/ledger, core/apps/payments and the event-writing part of core/apps/audit.
- Ledger: per-tenant accounts from spec/ledger_accounts.yaml (AP:<vendor>, AR:<customer> created on
  demand); tables ledger_account, ledger_journal_entry, ledger_posting (BIGINT minor units + currency,
  signed: debit > 0, credit < 0), ledger_balance (balance rows updated in the same transaction).
  PostgreSQL enforces: deferred constraint trigger (DEFERRABLE INITIALLY DEFERRED) rejecting any journal
  entry whose postings do not sum to zero per currency at COMMIT; CHECK (balance >= 0) for Bank:available,
  Bank:reserved, Bank:in_transit. Posting templates from the spec only; a reversal is a NEW journal entry.
- Payments: payments_payment with state from spec/state_machine.yaml (loaded at startup, never
  hard-coded), reject_reason, payee_reportable; payments_tenant_limits (one row per tenant, the lock row:
  lock_tenant_limits(tenant_id) = SELECT ... FOR UPDATE); payments_outbox (transactional outbox rows);
  payments_idempotency_key (UNIQUE(tenant_id, key), request SHA-256, stored response) with a helper the API
  layer will use; partial UNIQUE(tenant_id, invoice_fingerprint) over live payments (live = not DENIED,
  FAILED, or REJECTED with fraud_or_invalid). invoice_fingerprint = SHA-256(vendor_id | normalised ref |
  amount_minor | currency).
- transition(payment, to, trigger, actor, context): one DB transaction = state row + postings (template and
  posting_condition from the spec) + exactly one audit event + outbox row when the spec says so; atomic
  chains PROPOSED->CLEARED->RESERVED and HELD->APPROVED->RESERVED; guards are a registry of callables
  (M2/M8 plug in real ones later; implement the structural ones and reject_reason rules now); any pair not
  in the spec raises ILLEGAL_TRANSITION (typed error with the errors.yaml code).
- Audit: audit_event append-only, per-tenant seq without gaps, hash = SHA-256(prev_hash || RFC 8785 JSON of
  the event without hash) using the rfc8785 library; genesis prev_hash = 64 zeros.
- Tests: Hypothesis random event sequences keep every invariant (sum zero, non-negative bank accounts,
  balances == sum of postings, audit chain recomputes); every illegal transition raises (generate from the
  spec); concurrent reserve test cannot overdraw (two threads, TransactionTestCase); REJECTED not_now can
  be re-proposed, fraud_or_invalid cannot and posts invoice_voided; and, on the REAL tables, the app role
  cannot UPDATE/DELETE/TRUNCATE ledger_posting, ledger_journal_entry or audit_event, cannot DISABLE TRIGGER,
  and sees zero rows without app.tenant_id (copy the pattern of core/tests/test_db_roles.py).
- mypy strict applies to apps.ledger and apps.payments (already configured).
```

### M2 (session B): guardian

```
Build M2 in core/apps/guardian and core/apps/keystore.
- Pure decide(inputs) -> Decision over typed inputs (pydantic or dataclasses): proposal, vendor, registry
  account snapshot (incl. changed_at, first_used_at), invoice extraction (bank details on invoice,
  untrusted_text), payment history, rolling totals, forecast buffer result, network result
  (SKIP | UNAVAILABLE | HIT{reporters} | MISS), tenant frozen flag, now. No DB access inside decide().
- Each reason code in spec/policy.yaml is its own function; policy loader reads spec/policy.yaml and
  exposes policy_version; verdict via verdict_rules R1-R5 exactly; score = min(100, sum of signal weights);
  network codes never DENY; NETWORK_UNAVAILABLE only for new/changed payees.
- Detectors: TR39 confusable skeleton via a maintained library (e.g. confusable_homoglyphs) + Damerau-
  Levenshtein <= 2; LOOKALIKE_ACCOUNT prefix/suffix 4; robust z (median/MAD*1.4826) > 3.5 with >= 5
  history; DUPLICATE_SUSPECT, SPLIT_PATTERN, INJECTION_MARKERS per the policy detectors section.
- Decision canonicalisation with RFC 8785 (rfc8785 library), inputs_sha256 over the canonical inputs
  (the inputs must contain the registry account snapshot so approval can detect PAYEE_CHANGED, D-029);
  Ed25519 sign/verify through a KeyStore port (pyca/cryptography only; file-backed dev implementation with
  per-tenant keys and key_id; tests use a temporary directory, never the shared dev-keys dir).
- recheck_for_approval(...) (D-029): hard rules + frozen + payee account unchanged (via the stored canonical
  inputs and inputs_sha256) + funds available; returns typed failures (HARD_RULE_FAILED, TENANT_FROZEN,
  PAYEE_CHANGED, FUNDS_UNAVAILABLE) and warnings (CASH_BUFFER_BREACH requires acknowledgement); never
  re-applies agent caps.
- guardian_decision table: append-only (db_table fixed), UNIQUE(nonce); store the canonical inputs JSON.
- Tests: table-driven per reason code and per verdict path; replay determinism (same inputs +
  policy_version -> identical canonical bytes before signing); signature verifies, tampering fails;
  Decision validates against spec/schemas/Decision.json; mypy strict (apps.guardian, apps.keystore).
```

### M3 (session C): bank sandbox

```
Build M3 in bank_sandbox/ (apps accounts, transfers, statements) to spec/openapi-bank.yaml. SIMULATED bank.
- Accounts with balances (no tenant_id column), seeded by a management command (synthetic, labelled).
- POST /v1/transfers idempotent on end_to_end_id (same body -> 200 same transfer; different body -> 409
  IDEMPOTENCY_MISMATCH via RFC 8785 request_sha256); synchronous settlement with an Ed25519-signed Receipt
  (bank_attestation key from SAFEPAY_KEYS_DIR/ed25519/bank_attestation.pem; signature over RFC 8785 JSON
  without sig); INSUFFICIENT_FUNDS produces a FAILED receipt; GET /v1/transfers/{e2e}; GET /v1/accounts/{id};
  GET /v1/statements?account_id&date signed daily statement (closing = opening + credits - debits);
  POST /admin/fail-next (fail | timeout modes).
- Request-signature auth middleware (DECISIONS.md D-012) verifying core_service.pub; skew 300 s; nonce
  single-use; RFC 9457 problem+json errors with errors.yaml codes.
- Append-only tables transfers_receipt and statements_statement (db_table fixed).
- Tests: same e2e id twice -> one transfer; receipts and statements validate against spec/schemas and their
  signatures verify; forged/expired/replayed request signatures rejected; injected failures produce FAILED.
```

### M4 (session D): intel

```
Build M4 in intel/ (apps members, oprf, reports, snapshots) to spec/openapi-intel.yaml. mypy strict.
- FIRST: wrap voprf 0.2 behind intel/apps/oprf/port.py (OprfServer, OprfClient), suite ristretto255-SHA512,
  VOPRF mode, and prove it against spec/vectors/rfc9497/allVectors.json (identifier ristretto255-SHA512,
  mode 1: skSm from seed/keyInfo, BlindedElement, EvaluationElement, Proof, Output, including the batch
  vector). Inspect the binding (python -c "import voprf; help(voprf.ristretto)"). HARD DEADLINE 15:00 Baku:
  if the vectors do not pass by then, switch to the fallback pyoprf (OPRF mode, non-verifiable, LGPLv3),
  prove it against the mode-0 vectors, and record the downgrade as a known risk in docs/DECISIONS.md
  (D-030) and in your report. Never implement curve arithmetic, hash-to-group or DLEQ yourself.
- Epoch keys (7 days), GET /v1/epochs/current, POST /v1/oprf/evaluate with member Ed25519 request
  signatures (D-012) and quota 500/member/day (429 QUOTA_EXCEEDED).
- Report admission per spec/schemas/Report.json: member signature, bank receipt signature (bank
  attestation public key from SAFEPAY_KEYS_DIR/ed25519/bank_attestation.pub), SETTLED, payee == creditor,
  debtor account belongs to the member, <= 90 days, one count per (member, payee), 30-day expiry,
  retraction by the reporter only. Store receipts in reports_receipt (append-only).
- Signed snapshots (snapshots_snapshot, append-only) with 16-byte tags and pk_e pinned.
- Members created by a management command (admission control).
- Tests: CFRG vectors pass; forged/old/mismatched receipts rejected; quota returns 429; snapshot signature
  verifies; retraction by a non-reporter returns 403 NOT_REPORTER.
```

### M5 (session E): sim twins

```
Build M5 in sim/safepay_sim/twins (pure Python, no database, no network). Everything is SYNTHETIC.
- 8 seeded SMEs (retail, construction, restaurant, logistics, pharmacy, agriculture, IT services,
  textiles), 90 simulated days: vendors with registry accounts (valid mod-97 IBANs), customers and AR,
  recurring opex, opening balance, invoices as email bodies and text PDFs.
- Attack and ambiguous invoices for the scenario classes in spec/schemas/EvidenceRecord.json
  (hidden instruction, bank-detail swap, look-alike vendor/account, duplicate/near-duplicate, split, ...).
- Ground-truth labels: benign/attack/ambiguous, expected verdict, expected forecast LOW_BUFFER/SHORTFALL
  weeks, oracle analysis answers (DPO, DSO, burn, top vendors) computed independently in sim (not by core).
- Output: deterministic files (sorted keys, fixed float-free formatting) under an output dir; CLI
  `python -m safepay_sim.twins --seed 42 --out <dir>`.
- Tests: byte-identical output for the same seed (hash the whole tree); different seeds differ; label schema
  validates (define it with JSON Schema in sim/, do not edit spec/); all amounts are int minor units.
```

## 5. Gate G1 merge (lead, main checkout)

```bash
cd C:/Users/Asus/OneDrive/Desktop/safepay
for m in m1 m2 m3 m4 m5; do git merge --no-ff $m -m "merge($m): G1"; done   # re-lock: uv lock on conflict
make migrate && make verify && make up
git worktree remove C:/Users/Asus/safepay-wt/<m>   # after merge, when no longer needed
```
