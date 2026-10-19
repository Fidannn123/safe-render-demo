# DECISIONS.md: decision log

Decisions taken where the four source documents (CLAUDE.md, architecture.md, and, now under
`docs/internal/`, COMPETITION.md and BUILD_PLAN.md) were silent, ambiguous or in tension. Newest last. IDs are referenced from `spec/`.
Status: **accepted** (decided, in spec/code), **open** (needs the human).

## G0: spec freeze and skeleton (19 Oct 2026)

### D-001 Verdict resolution (accepted; from BUILD_PLAN.md)
architecture.md §2.3 pseudocode allows ALLOW for a known payee with score < 50, while §3.2 requires
score < 30 for T1. Rule, in order: any hard rule → DENY (tier null); any T3 trigger or score ≥ 50 →
HOLD (T3); new/changed payee (score < 50, within new-payee caps) → GUARDED (T2); known payee and
score < 30 → ALLOW (T1); known payee and 30 ≤ score < 50 → GUARDED (T2, cool-off + owner veto).
Encoded as `verdict_rules` R1-R5 in `spec/policy.yaml`; tested in `tests/spec/test_policy.py`.

### D-002 Makefile and CI location (accepted)
CLAUDE.md puts `Makefile` and `.github/workflows/` under `ops/`. GitHub only runs workflows from the
root `.github/workflows/`, and `make <target>` needs a root Makefile. Targets live in `ops/Makefile`;
the root `Makefile` is `include ops/Makefile`; CI is `.github/workflows/ci.yml`.

### D-003 pnpm workspace layout (accepted)
BUILD_PLAN.md says `packages/*`; CLAUDE.md layout places the client at `console/packages/api-client/`.
CLAUDE.md wins: workspace globs are `console` and `console/packages/*`.

### D-004 Postings for DENIED and REJECTED (accepted with conditions by the human, 19 Oct)
architecture.md §2.4 says "approver rejects" releases funds, but a HELD payment never had funds
reserved (reserve happens only on ALLOW/GUARDED or APPROVE). DENIED and REJECTED therefore post no
bank movement. Because `invoice_accepted` (Expense/COGS ↔ AP) is posted at extraction, a denied or
rejected invoice posts `invoice_voided` (reverse accrual) when, and only when, it was accrued.
Conditions set by the human: (1) a reversal is always a NEW balanced journal entry, never an edit;
(2) REJECTED requires `reject_reason` in {`fraud_or_invalid`, `not_now`}; (3) `not_now` keeps the AP
accrual open and the invoice may be proposed again (new transition REJECTED→PROPOSED, new Decision);
(4) `fraud_or_invalid` reverses the accrual, is final, and marks the payee reportable (intel report).
REJECTED is therefore terminal only when `reject_reason = fraud_or_invalid` (`terminal_when`).
`invoice_accepted` is posted only if the extracted vendor is bound to the registry
(`posting_condition: vendor_bound`). FAILED keeps the AP liability (the debt is still owed).

### D-005 Atomic transition chains (accepted)
"One transition = one transaction" conflicts with "reserve in the same transaction as the decision".
`state_machine.yaml` declares `atomic_chains`: PROPOSED→CLEARED→RESERVED and
HELD→APPROVED→RESERVED commit in one transaction, one audit event per transition. If the reserve
violates `CHECK (Bank:available >= 0)` the whole transaction rolls back.

### D-006 Multiple approvals (accepted)
There is one APPROVED state but "two approvers above the high cap". A HELD payment stays HELD until
`approvals_required` distinct approvers (never the initiator) have approved; the final approval
performs HELD→APPROVED→RESERVED.

### D-007 Two-approver threshold (revised by the human, 19 Oct)
Amounts **at or above** `two_approver_threshold` (15,000.00) need two distinct approvers, neither of
them the initiator (supersedes the G0 "strictly above" reading).

### D-008 Core endpoints added to the frozen list (accepted by the human)
Needed by flows that the documents require but did not list:
`POST /payments/{id}/veto` (T2 owner veto), `POST /vendors/{id}/bank-account-change/{change_id}/confirm`
(second person), `POST /auth/login | /auth/logout | /auth/step-up`, `POST /auth/agent-tokens`,
`DELETE /auth/agent-tokens/{id}`, `GET|POST /vendors`, `GET /vendors/{id}` (lookup_vendor tool),
`GET /payments` (approvals queue), `GET /cashflow/schedule` (task 2, schedule payables).
Vendors are created **without** a bank account; accounts are added only via the bank-account-change
flow (owner + step-up + call-back attestation, then a different person confirms). The M5 seed
command may create twin vendors with verified accounts directly; this bypass exists only in the
seeding management command and is labelled as synthetic setup.

### D-009 Intel and bank endpoints added (accepted)
Intel: `GET /v1/epochs/current` (epoch + pk_e; pk_e is also pinned inside the signed snapshot).
Members are admission-controlled: created by a management command (KYB in production), so there is
no public member-registration endpoint. Bank: `GET /v1/accounts/{id}` (balance for recon and demo).
`/admin/fail-next` gains `mode: timeout` for the M9 chaos test.

### D-010 Ledger accounts beyond architecture.md (accepted by the human)
The forecast (§2.6) needs receivables and an opening balance; DSO needs AR. Added `AR:<customer>`,
`Revenue`, `Equity:opening` and templates `opening_balance`, `ar_invoice_issued`, `customer_receipt`,
`direct_debit_opex`. They are only used by synthetic twins and reconciliation; bank lines they
create carry an `end_to_end_id` so recon can match them (no false MISSING_IN_LEDGER freezes).
Sign convention: posting amount positive = debit, negative = credit; balance per normal side.

### D-011 Decision fields (accepted)
architecture.md §6 field list kept exactly, plus `payment_id` (end-to-end id binding), `issued_at`
(TTL anchor), `key_id` (90-day key rotation). `tier` is null for DENY. The signature covers the RFC 8785
canonical JSON of every field except `sig`. Verdict/tier binding is enforced by the schema.

### D-012 Service request signing wire format (accepted)
architecture.md specifies "Ed25519-signed requests with timestamp + nonce" without a format. Headers
`X-SafePay-Key-Id`, `X-SafePay-Timestamp` (unix seconds), `X-SafePay-Nonce` (hex),
`X-SafePay-Signature` (base64) over
`METHOD \n PATH[?QUERY] \n TIMESTAMP \n NONCE \n hex(SHA-256(body))`; skew ≤ 300 s; nonce single-use
inside the skew window. mTLS in production.

### D-013 Database roles (resolved by the human, 19 Oct: owner/app split)
As instructed: three databases, three login roles (`safepay_core`, `safepay_intel`, `safepay_bank`),
each owning its database, CONNECT revoked from PUBLIC (verified: core role cannot connect to the
intel DB). Roles have CREATEDB only so Django's test runner can create `test_<db>` (dev/CI only).
Resolution: per service `safepay_<svc>_owner` (owns database and schema; used only by the one-shot
`migrate` compose service, and by the test runner to create test databases) and `safepay_<svc>_app`
(runtime for web and worker: NOSUPERUSER, NOBYPASSRLS, NOINHERIT, owns nothing). The app role has
SELECT and INSERT only on ledger, postings, audit, decisions and receipts; never UPDATE, DELETE,
TRUNCATE or ALTER on them. FORCE RLS and the append-only triggers stay. Implementation: D-033.

### D-014 Recon SLA (accepted)
"MISSING_IN_BANK beyond SLA" had no value: `missing_in_bank_sla_seconds: 3600`.

### D-015 Detector parameters not in BUILD_PLAN.md (accepted by the human)
`LOOKALIKE_ACCOUNT` = shares the first or last 4 BBAN characters with a known account;
`AMOUNT_ANOMALY` uses MAD scaled by 1.4826 and needs ≥ 5 prior payments to this vendor (else not
evaluated); `OVER_NEW_PAYEE_ROLLING_24H_CAP` is a separate T3 code for `new_payee_rolling_24h_cap`;
rolling caps count payments in RESERVED, SUBMITTED, SETTLED, RECONCILED. Human approval semantics: D-029.

### D-016 Idempotency scope (accepted)
Idempotency-Key is required on every mutating core POST. Exempt (no stored side effect, or session
endpoints): `/auth/login`, `/auth/logout`, `/auth/step-up`, `/financing/match` (pure). The bank dedups on
`end_to_end_id`; intel dedups reports on `report_id` and counts one per (member, payee). Enforced by
`tests/spec/test_openapi.py`.

### D-017 Custom user model at G0 (accepted)
Django requires `AUTH_USER_MODEL` before the first migration. `accounts.User(AbstractUser)` with a
UUID primary key exists from G0; M8 extends it. This is the only model at G0 (no business logic).

### D-018 Secrets as generated files (accepted)
No secret is ever in `.env`. `ops/scripts/gen_dev_keys.py` writes DB passwords, Django keys, the
agent-token pepper, the data KEK, the blind-index key and Ed25519 service keys to `ops/dev-keys/`
(gitignored). The compose `keygen` one-shot runs it first, so a clean machine needs only Docker.
Services read `*_FILE` paths. Files are 0644 so containers with different uids can read the bind
mount: dev/demo only; production uses KMS/HSM via the KeyStore port. Per-tenant Decision/audit keys,
member keys and OPRF epoch keys are created at runtime by M2/M4.

### D-019 Problem codes (accepted)
`type = urn:safepay:problem:<CODE>` (no domain we do not own). BUILD_PLAN.md's codes plus the codes
the frozen endpoints need (auth, idempotency, signing, intel admission, bank). `Problem.code` enum must
equal `errors.yaml` keys; each operation lists its codes in `x-problem-codes`.

### D-020 Decimal values (accepted)
Money is int64 minor units. Rates (APR) and analysis values (DPO/DSO days) are decimal **strings**
(parse with `decimal.Decimal`; rounding half-even; APR 4 places, days 1 place). No floats in policy or
API money fields (tested).

### D-021 Toolchain pins (accepted)
Django 5.2.18 (LTS), django-ninja 1.7.1, psycopg 3.3.6, argon2-cffi 25.1.0, cryptography 50.0.2,
voprf 0.2.0 (has cp313 wheels for linux and windows), rfc8785 0.1.4 (passes all six RFC 8785 reference
cases), mypy 2.4 + django-stubs 5.2.9. TypeScript **5.9.3**, not 7.x: typescript-eslint 8.71 supports
`<6.1` and openapi-typescript 7.13 requires `^5`. pnpm **10.34.6** (latest 10.x older than 14 days);
`minimumReleaseAge: 20160` minutes in `pnpm-workspace.yaml`. Images: `postgres:18.6-bookworm`,
`python:3.13.16-slim-bookworm`, `node:22.23.3-alpine`, `nginx:1.30.6-alpine`, `gitleaks:v8.29.1`.

### D-022 No CDN docs page (accepted)
django-ninja's default Swagger UI loads assets from a CDN (unreachable for some reviewers):
`docs_url=None` in all three APIs; `openapi.json` is still served. A bundled docs page is optional
for Phase 3.

### D-023 Test vectors (accepted)
RFC 9497 from the CFRG repo `poc/vectors/allVectors.json` at a pinned commit; RFC 8785 cases from
cyberphone/json-canonicalization (the reference cited by the RFC) at a pinned commit; RFC 8032 §7.1
parsed from the RFC text. SHA-256 manifest in `spec/vectors/SOURCES.sha256`, re-checked by tests.
Vector files are `-text` in `.gitattributes` (byte-exact). gitleaks allowlists only `spec/vectors/`
because RFC 8032 publishes its test private keys.

### D-024 Stub make targets fail loudly (accepted)
`make seed | demo | evidence | verify-evidence | verify-audit | clean-clone` exit 3 with
"NOT IMPLEMENTED YET (owner …)" so a stub can never be mistaken for a pass.

### D-025 Intel and bank are service-only (accepted)
No `contrib.auth`, sessions or admin in intel or bank: they have no human users and authenticate
callers by Ed25519 request signatures. Argon2 is still configured first in `PASSWORD_HASHERS`.

### D-026 Packaging details (accepted)
One Python image for every Python service (`uv sync --frozen --no-dev`), non-root uid 10001. All
ports bind to 127.0.0.1; PostgreSQL host port 55432 (avoids clashing with a local PostgreSQL).
The console nginx proxies `/api/` to core-web (same origin, no CORS) and sends a strict CSP.
PostgreSQL health is a TCP check, so it turns healthy only after the init script finished.

### D-027 T4 freeze semantics (accepted)
Freeze transitions no payment. Payments stay where they are: RESERVED payments are not delivered
while frozen (the outbox refuses), until unfreeze or T2 veto. New proposals while frozen are DENIED
with `TENANT_FROZEN`. Freeze revokes all agent tokens. Unfreeze needs owner + step-up.

### D-028 Bank sandbox settlement (accepted)
The sandbox settles synchronously (201 with a signed receipt) unless failure injection is armed;
`mode: timeout` accepts the transfer without answering so M9 can test retries and recovery.

## G0 decisions from the human (19 Oct 2026, ~11:40 Baku)

### D-029 Human approval of a HOLD
Approving a HOLD does NOT re-apply the agent caps that caused it (else an over-cap payment could never be
paid). When the final required approval arrives, ONE transaction under the tenant-limits row lock
(`SELECT ... FOR UPDATE`) re-checks: every hard (DENY) rule (`409 HARD_RULE_FAILED`), the tenant is not
frozen (`423 TENANT_FROZEN`), the payee registry account is unchanged since the Decision, compared with the
decide() inputs bound by `inputs_sha256` (`409 PAYEE_CHANGED`), and funds are available (`409
FUNDS_UNAVAILABLE`). Any failure leaves the payment HELD. CASH_BUFFER_BREACH is shown in
`Payment.approval_warnings` and must be acknowledged in `ApproveBody.acknowledged_warnings` (`409
WARNING_NOT_ACKNOWLEDGED`). At or above `two_approver_threshold`: two distinct approvers, neither the
initiator (D-007). Human-approved amounts count toward later agent rolling-24 h totals. The audit event
records approver ids, reasons and acknowledged warnings. The approve endpoint no longer lists CAP_EXCEEDED.

### D-030 Cuts
AnchorPort dropped entirely (Checkpoint.anchor removed from the spec; no on-chain component at all).
Passkeys dropped. Financing matcher: exactly 3 synthetic products (one per type; `minItems = maxItems = 3`).
Console: approvals queue + decision explainer + metrics page + freeze button only. If the M4 VOPRF wrapper
does not pass the CFRG vectors by 15:00 Baku, M4 switches to the documented fallback (`pyoprf`, OPRF mode,
non-verifiable, LGPLv3), disclosed as a downgrade and recorded as a known risk.

### D-031 Re-planned schedule (Baku)
About 7.5 h behind plan. G1 by 17:30, G2 by 00:30, G3 by 05:00, v1 submission 07:59; final 17:59 unchanged.
Phase 1 runs as parallel sessions in git worktrees (branches m1..m5), merged to main at each gate
(that guide was removed after the build).

### D-032 LLM configuration
Mock stays the default everywhere (`SAFEPAY_LLM=mock`; tests and CI force it). For the demo the
Anthropic provider uses `SAFEPAY_LLM_MODEL=claude-sonnet-5-5` (default value in settings, compose and
`.env.example`); the human puts `ANTHROPIC_API_KEY` in a local, gitignored `.env`. Superseded in part by
D-050 (SDK client, default `claude-opus-5-5`).

### D-033 How the database policy is enforced (implementation of D-013)
`<project>/db_policy.py` runs after every `migrate` (post_migrate of the project AppConfig), as the owner,
in one transaction, and converges idempotently: REVOKE ALL then GRANT SELECT, INSERT, UPDATE on ordinary
tables; SELECT, INSERT only (plus a BEFORE UPDATE OR DELETE trigger that raises for every role) on
`SAFEPAY_APPEND_ONLY_TABLES`; SELECT only on `django_migrations`; DELETE only on
`SAFEPAY_DELETE_ALLOWED_TABLES` (`django_session`, needed for logout); never TRUNCATE/REFERENCES/TRIGGER
or DDL. Every table with a `tenant_id` column gets ENABLE + FORCE RLS with policy
`tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid` for USING and WITH CHECK. CONNECT on
each database is revoked from PUBLIC and granted to its app role only; app roles cannot connect to the
`postgres` maintenance database. Reading of "no UPDATE, DELETE, TRUNCATE or ALTER anywhere": no
TRUNCATE/ALTER anywhere and no UPDATE/DELETE on the append-only tables; UPDATE stays on mutable state rows
(payments, outbox, balances), which the state machine requires. Append-only table names are a contract
for module owners: core `ledger_journal_entry`, `ledger_posting`, `audit_event`, `audit_checkpoint`,
`guardian_decision`, `bank_adapter_receipt`; intel `snapshots_snapshot`, `reports_receipt`; bank
`transfers_receipt`, `statements_statement`. Proven now by test-only probe tables
(`core/tests/test_db_roles.py`); M1 repeats the same assertions on the real ledger and audit tables.

### D-034 Parallel test databases
With `SAFEPAY_TEST_DB_SUFFIX=m1..m5` each project's test database is `test_safepay_<svc>_<suffix>`, so
worktrees can run tests concurrently against the one shared PostgreSQL. With the suffix set, `make db-up`
never touches docker compose; only the main checkout runs compose. Worktrees read the main checkout's
dev keys through `SAFEPAY_KEYS_DIR`.

## G2/G3 completion (19 Oct 2026): defects found by running the stack, and the harness design

### D-035 Request bodies (defect fixed)
Every POST view declared `payload: dict[str, Any]`, which django-ninja reads as a required *query*
parameter, so login, step-up, agent tokens, vendors, invoice ingest, proposals, approve/reject/veto,
freeze/unfreeze and financing all answered 422. Bodies are now `Body[dict[str, Any]]` and are still
validated against `spec/` by `validate_payload`. Regression: `core/tests/test_api_flows.py`.

### D-036 Who counts as the initiator for four-eyes (defect fixed, M8: human review)
Only agents propose, so `Payment.initiated_by` is always an agent token and the self-approval check
compared a user id with a token id: it could never fire. The accountable initiator is now the human who
issued that token (`AgentToken.created_by`), in `approvals.service.accountable_initiator`, used by approve,
reject and veto; `Approval.initiator_id` (DB CHECK approver <> initiator) stores that user.

### D-037 Missing migrations (defect fixed)
`tenants`, `vendors`, `invoices`, `cashflow` and `approvals` had no `migrations` package, so `migrate`
never created their tables (the test runner hid this with syncdb), and `accounts`/`payments` had model
fields without migrations. All are generated; `makemigrations --check` is clean.

### D-038 Tenant middleware scope (defect fixed)
The tenant transaction wrapped every request, so `/healthz` could not report a degraded database and
anonymous reads needed one. Non-`/api/` paths and anonymous safe-method requests now skip it.

### D-039 Evidence harness (M10) design
`make evidence` = `python -m safepay_sim.harness evidence`: fresh run plan (tenants, bank accounts, member
keys; private keys in `ops/dev-keys/evidence/`), bank accounts and network members registered through
their management commands, then `manage.py run_evidence` (settings `evidence`) creates a fresh database
`safepay_core_evidence_<seed>` as the owner, migrates it, and runs as the app role. Requests go through the
real API in-process (Django test client: middleware, token auth, idempotency, RLS) and the real outbox
worker; the bank and intel are the live services over signed HTTP.
- Time: core runs on a simulated clock (`apps.evidence.clock`, pins `django.utils.timezone.now`) so the
  twins' scenario times, 24 h windows and T2 cool-offs mean what the labels assume; each configuration
  replays the same timeline. Bank and intel keep wall-clock time; network lookups use wall-clock time.
- B0 = the same mock extractor with direct transfer rights: it pays the account printed on the invoice.
  B1 = network consulted but holding no reports. B2 = B1 after members file bank-attested reports; the
  reports are retracted when the run ends so the shared network is left as found.
- Refusals before the Guardian count as the labelled DENY when nothing is paid: `BLOCKED_AT_API`
  (registry binding refused a vendor the planner guessed) and `BLOCKED_AT_EXTRACTION` (PostgreSQL's
  live-fingerprint constraint refused an exact duplicate). Records keep the observed layer.
- T2 payments execute after the cool-off because the harness never vetoes; this is reported as residual
  loss, per scenario, in `EVIDENCE.md`.
- `make verify-evidence` needs no database: it validates `ops.jsonl` against the schema, checks every
  Decision, receipt and statement signature, recomputes each tenant's audit chain with
  `ops/scripts/verify_audit_export.py`, and recomputes `metrics.json`.

### D-040 Seeding (D-008 bypass, implemented)
`manage.py seed_twins` replays each twin's `ledger.json` through the spec posting templates with the clock
pinned to each event's date (as_of mapped to today), creates owner/approver/accountant users with TOTP,
and a member key. Each member also owns a "legacy" sandbox account that core does not reconcile; the demo's
`report_payee` records the reporter's earlier loss from it, so the live reconciler does not freeze the
reporter for an unexplained debit.

### D-041 Snapshot validity margin (intel)
Intel republishes a snapshot when fewer than 60 s of validity remain, so a client never receives one
that expires before it can check it (a lookup then failed closed as NETWORK_UNAVAILABLE).

### D-042 Smaller changes
`RemoteError.problem_code` keeps a pattern-checked remote problem code (never a body); outbox rows record
typed `ProblemError` codes. Twin generator: the reported-payee vendor is the cheapest spare vendor (a costly
one could not be paid under the new-payee cap without AMOUNT_ANOMALY), and look-alike names fall back to
digit substitutions when a name has none of the usual letters. `ops/scripts/native_stack.py` and
`make native-*` run the stack without Docker (dev only).

### D-043 Docker path, clean-clone and generated README results
Found by reading the compose setup, since Docker is not available on the machine used for G2/G3:
- In Docker the harness runs host-file commands (`run_evidence`, bank and intel registration) as the host
  user, and `live` commands (`seed_twins`, `report_payee`, `verify_audit`) as the image's service user,
  which owns the core volume (tenant keystore, member keys, PII key). `seed_twins --out -` prints the
  credentials document so the harness writes the file on the host; no container writes the host repo.
- core-web mounts `../evidence` read-only, so `GET /api/v1/evidence/metrics` (console Metrics page) sees
  the bundle `make evidence` writes on the host.
- `make clean-clone` (ops/scripts/clean_clone.sh): fresh copy in a temp directory, then `make up`, seed,
  demo, evidence, verify-evidence, verify-audit and verify, under `COMPOSE_PROJECT_NAME=safepay-clean-clone`
  so it never touches the user's stack or database volume.
- CI runs the Docker path on every push after `make verify` and uploads `evidence/run-42/`.
- README "Results" is rewritten by `make evidence` from metrics.json (between `results:start/end`
  markers); `make verify-evidence` fails when the README shows other numbers for the same run.
- native_stack: `pg_ctl start` gets null std handles. On Windows pg_ctl keeps a cmd.exe wrapper alive
  for the server's lifetime, and an inherited pipe made `make native-reset | tail` hang.

### D-044 First Docker run (Linux) and the security self-assessment
- The whole Docker path ran for the first time: `make up`, seed, demo, evidence, verify-evidence,
  verify-audit, verify and `make clean-clone` pass, and a held payment was approved in the console on :8080.
- Defect fixed: when the host user is root, the harness ran non-live commands as image user 10001,
  which cannot write a root-owned checkout, so `make evidence` failed. Non-live commands now always run
  as the host uid and gid, root included.
- CI: gitleaks flagged `sign_key: Ed25519PrivateKey` type annotations as generic API keys. `.gitleaks.toml`
  allowlists that annotation pattern only.
- M6 had no tests. `core/apps/agent/tests/test_injection.py` covers its gate: an injection corpus never
  reaches a planner tool argument, raw text stays tagged `untrusted`, extra model fields are rejected.
- `docs/SECURITY.md` (deliverable 05) lists where the prototype is weaker than architecture.md: one PII
  key per deployment instead of per-tenant DEKs under a KEK, no velocity freeze, and the large-candidate
  privacy test is not built.

- `make down-volumes` now also deletes `ops/dev-keys/demo` (the users of the deleted database), as
  `native-reset` already did; before, `make seed` refused after a reset and gave no way to force it.
- `make hold TWIN=<twin>` leaves one HELD bank-detail-swap payment for the console, because `make demo`
  rejects its own attack payments.
- `make tech-doc` (deliverable 01) renders `docs/architecture.md` without section 0 and the schedule,
  a "differs from this design" section, and the run's generated EVIDENCE.md sections, to PDF via
  headless Chromium. Mermaid is a root pnpm dev dependency so diagrams render offline.
- `docs/DECK.md` and `docs/DEMO_SCRIPT.md`: slide outline and video beats; numbers are placeholders
  pointing at generated evidence cells.

### D-045 Policy 2026.10.19-2: new-payee splits and single reports are held (spec unfrozen by the human)
The human asked to remove the known T2 losses and left the trade-off to us ("do the best for the
product"), which we take as the go-ahead to change `spec/policy.yaml` for this purpose only.
- `detectors.split_pattern.new_payee`: for a new or changed payee, 2 payments within 24 h summing over
  `new_payee_cap` are a SPLIT_PATTERN (known payees keep 3 payments over `per_tx_cap`).
- Weights: SPLIT_PATTERN 30 to 35 and NETWORK_REPORTED_1 25 to 35, so either one plus
  NEW_OR_CHANGED_PAYEE (15) reaches the HOLD threshold of 50. Known-payee splits stay GUARDED (35 < 50).
- Network codes still never DENY. A single false report can force a HOLD on a new payee, which the
  architecture already accepts ("can force HOLD").
- The first part of a new-payee split remains possible residual loss: nothing distinguishes it from an
  honest first payment. The sim oracle (`sim/safepay_sim/twins/policy.py`) mirrors the rule.

### D-046 Automatic velocity freeze
`spec/policy.yaml velocity`: an agent token that makes `refusals_to_freeze` (3) refused requests within
`window_seconds` (600) freezes its tenant through the normal T4 path (actor `system:velocity-monitor`,
all agent tokens revoked, unfreeze needs owner + step-up). A refusal is a 403 on an agent token or a
DENIED proposal. `TenantMiddleware` records it as audit event AGENT_REQUEST_REFUSED in its own
transaction after the refused request rolled back (`core/apps/approvals/velocity.py`). Human 403s do not
count. The STOLEN_TOKEN_DIRECT_API scenario now expects the freeze; the harness then unfreezes as owner.

### D-047 Envelope encryption of personal data (crypto wrapper: human review)
`core/apps/keystore/pii.py`: one AES-256 data key per tenant, created on first use, stored only wrapped
under a deployment KEK with AES key wrap (RFC 3394, pyca). Ciphertext `v2:` + base64(nonce | ct | tag),
associated data `safepay/pii/v2|<tenant>`. v1 ciphertexts (one deployment key) are still read. Files:
`tenant-deks/<tenant>.v1` next to the keystore (shared by web and worker in the core volume).

### D-048 Operator privacy attack in every evidence bundle
`run_evidence` writes the captured intel transcript and the true payee list; `manage.py privacy_attack`
(intel, which holds the epoch keys) computes, for 10^6 candidate IBANs, every PRF output and snapshot tag
per epoch, SHA-256 of the input and the IBAN text, and matches them against all blinded elements and the
operator's own answers. A positive control runs the same attack on a SHA-256 lookup and must recover
every payee. Results go into `privacy.json` and metrics; `make verify-evidence` fails if anything was
recovered or the control failed. Candidate count: `--privacy-candidates` (default 10^6).

### D-049 OPRF power-on self-test
`intel/apps/oprf/selftest.py` runs from `OprfConfig.ready()`: RFC 9497 key derivation, Evaluate,
BlindEvaluate, a blind/finalize round trip and one tampered proof. Any mismatch raises and the service
does not start. Mitigates (does not remove) the unaudited-binding risk.

### D-050 Real-LLM extractor on the Anthropic SDK, and its measurement
The extractor's real-provider branch uses the official `anthropic` SDK (added to core) instead of raw
urllib: one tool-free `beta.messages.create` call with structured output (`output_config.format`
JSON schema that holds facts only), `effort: low`, server-side `fallbacks: "default"`. Anything but
`end_turn` (refusal, truncation), invalid JSON or a field outside the schema stops the extraction.
Default model `claude-opus-5-5` (env `SAFEPAY_LLM_MODEL`). `make evidence-llm` (`manage.py measure_llm`)
measures field accuracy, printed-bank-detail reporting and whether an injection reaches a planner tool
argument on every twin invoice; `PROVIDER=mock` gives the baseline. No key exists in the build
environment, so no real-model numbers are published yet.

### D-051 Twin generator seeds and repository clean-up
- Stressed calibration (seeds 50, 90): when the forecast already ends below the buffer without the big
  payable, the opening balance is lifted by that deficit at once instead of in fixed steps.
- Near-duplicate scenario (seeds 113, 148, 183, 198): the re-sent invoice is chosen among amounts that
  are not an AMOUNT_ANOMALY for the vendor. `sim/tests/test_twins.py::test_other_seeds_generate`.
- The harness regenerates `evidence/twins-<seed>` whenever it differs from what the current generator
  builds, so a stale directory can no longer feed old labels.
- Removed the parallel-session prompts (`docs/KICKOFF_PROMPT.md`, `docs/PARALLEL.md`) and the unused
  `ops/scripts/stub.sh`; moved `BUILD_PLAN.md` and `COMPETITION.md` to `docs/internal/`. README badges
  and `docs/assets/architecture.svg` are generated offline from Simple Icons (CC0) by scripts in
  `docs/assets/`.

### D-052 Console visual language
The console uses the MuhAI design language: a tinted ground, white cards on one hairline, a dark action
colour, lime for the active item, tint pills for statuses, a 92 px icon rail that becomes a bottom bar
on phones. Inter Tight, Syne (wordmark) and IBM Plex Mono are bundled as latin woff2 files from
`@fontsource` 5.3.0 under `console/src/assets/fonts/` with their OFL licences; nothing loads from a
CDN. Class names are unchanged, so the views needed no markup changes beyond the shell in `App.tsx`.

### D-053 Landing page at `/`, console at `/console`
The console bundle also serves a landing page, ported from the MuhAI landing (same sections, motion and
stylesheet, `console/src/landing.css`) with SafePay copy. `/` loads the landing and `/console` the
console; `main.tsx` picks one by path and loads it as its own chunk, so the two stylesheets never meet.
nginx and Vite already fall back to `index.html`, so no server change. Icons come from `lucide-react`
1.50.0 (ISC, bundled, pinned past the 14-day `minimumReleaseAge`), as in MuhAI. The slab and showcase
photographs are Unsplash images bundled under `console/public/img/` (licence note there). Figures on the
landing are illustrations on synthetic values, never measured results; the page says synthetic data and
simulated bank in the hero and the footer.

## Open questions for the human
None blocking. Human-only items: run `make evidence-llm` with an `ANTHROPIC_API_KEY` to publish real-model
numbers, eligibility/registration with the organizer, recording the demo video.
