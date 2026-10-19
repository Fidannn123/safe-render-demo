# BUILD_PLAN.md: ordered build plan

All times are **Baku (UTC+4)**. Fixed points: **v1 submit 20 Oct 07:59**, **final submit 20 Oct 17:59**, **deadline 20 Oct 19:59**. If you start late, compress the phases proportionally; the fixed points do not move.

Each phase ends at a **gate**. At a gate, stop, run `make verify`, and report pass/fail/cuts to the human before starting the next phase.

Parallelism: phases 1 and 2 list modules that can run as separate Claude Code sessions (separate terminals/worktrees), because they meet only through `spec/`. With one session, do them in the listed order.

---

## Phase 0: Spec freeze and skeleton (19 Oct 02:00-04:00) → Gate G0

Goal: every contract exists and the empty pipeline is green. **Write no business logic.**

1. Repo skeleton exactly as in `CLAUDE.md` (Repository layout). `pyproject.toml` with uv workspace members `core`, `intel`, `bank_sandbox`, `sim`; `pnpm-workspace.yaml` with `console`, `packages/*`.
2. `spec/` (the frozen contracts):
   - `openapi-core.yaml`, `openapi-intel.yaml`, `openapi-bank.yaml` with every endpoint in architecture.md §6 "Frozen interfaces", plus: `POST /api/v1/invoices` (ingest), `GET /api/v1/payments/{id}`, `GET /api/v1/decisions/{id}`, `GET /api/v1/alerts`, `POST /api/v1/financing/match`, `POST /api/v1/vendors/{id}/bank-account-change` (owner + second person + call-back attestation), `DELETE /v1/reports/{id}` (retraction), `GET /v1/transfers/{end_to_end_id}`, `GET /healthz` on each service.
   - `schemas/*.json` (JSON Schema 2020-12): `Invoice`, `ExtractedInvoice`, `Proposal`, `Decision` (exact fields in architecture.md §6), `Receipt`, `Statement`, `Report`, `Snapshot`, `AuditEvent`, `Checkpoint`, `EvidenceRecord`, `Problem` (RFC 9457).
   - `state_machine.yaml`: states and allowed transitions exactly as architecture.md §2.2 (include `RESERVED -> FAILED` for T2 veto).
   - `ledger_accounts.yaml`: per tenant `Bank:available`, `Bank:reserved`, `Bank:in_transit`, `AP:<vendor>`, `Expense`, `COGS`; posting templates from architecture.md §2.4.
   - `policy.yaml` with the defaults below, `policy_version: "2026.10.19-1"`.
   - `vectors/`: CFRG RFC 9497 test vectors (ristretto255-SHA512, VOPRF mode) from the CFRG repo `poc/vectors/allVectors.json` / RFC 9497 Appendix A; RFC 8785 canonical-JSON cases; Ed25519 fixtures.
   - `errors.yaml`: typed problem codes (`CAP_EXCEEDED`, `IDEMPOTENCY_MISMATCH`, `SELF_APPROVAL`, `STEP_UP_REQUIRED`, `TENANT_FROZEN`, `ILLEGAL_TRANSITION`, `DECISION_EXPIRED`, `DECISION_REPLAYED`, `QUOTA_EXCEEDED`, `RECEIPT_INVALID`, ...).
3. Three Django projects booting with empty apps, settings split `base/test/prod`, Argon2 hasher configured, `/healthz`.
4. `ops/docker-compose.yml` (postgres:18 with three databases and roles via init SQL; core-web; core-worker; intel; bank; console static), `.env.example`, `ops/dev-keys/` generator script (gitignored output).
5. Makefile targets from CLAUDE.md (stubs allowed), GitHub Actions running `make verify`, gitleaks, pip-audit, `pnpm audit`.
6. `docs/DECISIONS.md` started (record the policy resolution below).

**G0 passes when:** `docker compose up` brings all services healthy; `make verify` is green on the empty suite; schemas validate; OpenAPI files lint; human has reviewed `spec/`. Then spec is frozen.

### Default policy values (`spec/policy.yaml`; demo currency USD, amounts in minor units)

| Key | Default |
|---|---|
| per_tx_cap | 500000 (5,000.00) |
| rolling_24h_cap | 2000000 (20,000.00) |
| new_payee_cap | 100000 (1,000.00) per payment |
| new_payee_rolling_24h_cap | 200000 (2,000.00) |
| two_approver_threshold | 1500000 (15,000.00) |
| new_or_changed_window_days | 30 |
| score_hold_threshold | 50 (score ≥ 50 → HOLD) |
| score_t1_max | 29 (T1 requires score < 30) |
| buffer_floor | 500000 (5,000.00); buffer = max(floor, 2 weeks median opex) |
| early_pay_hurdle_apr | 0.12 |
| t2_cooloff_seconds | 600 (demo); 14400 (prod) |
| decision_ttl_seconds | 900 |
| step_up_otp_max_age_seconds | 300 |
| agent_token_ttl_seconds | 3600 |
| oprf_quota_per_member_per_day | 500 |
| oprf_epoch_days | 7 |
| snapshot_pull_seconds | 600 |
| report_expiry_days | 30; receipt_max_age_days 90 |
| outbox_max_retries | 6 (exponential backoff + jitter, then incident) |
| recon_interval_seconds | 300 + end-of-day |
| weights | BANK_DETAIL_MISMATCH 50, LOOKALIKE_VENDOR 50, LOOKALIKE_ACCOUNT 50, NETWORK_REPORTED_2PLUS 50, NETWORK_REPORTED_1 25, INJECTION_MARKERS 30, DUPLICATE_SUSPECT 30, SPLIT_PATTERN 30, AMOUNT_ANOMALY 20, NEW_OR_CHANGED_PAYEE 15 |

**Policy resolution (record in DECISIONS.md):** architecture §2.3 pseudocode allows ALLOW for a known payee with score < 50, while §3.2 requires score < 30 for T1. Rule: known payee and score < 30 → ALLOW (T1); known payee and 30 ≤ score < 50 → GUARDED (T2, cool-off + owner veto); new/changed payee and score < 50 and within new-payee caps → GUARDED; anything with a T3 trigger or score ≥ 50 → HOLD; any hard rule → DENY.

---

## Phase 1: Foundations in parallel (19 Oct 04:00-12:00) → Gate G1

| Session | Module | Build | Acceptance gate |
|---|---|---|---|
| A | **M1 ledger + payments** (`core/apps/ledger`, `payments`) | Accounts, journal entries, postings; deferred balance trigger; `CHECK balance >= 0`; INSERT-only roles + triggers; balance rows updated in-transaction; state machine loaded from `state_machine.yaml`; outbox table; idempotency-key store; invoice_fingerprint uniqueness | Hypothesis: random event sequences keep all invariants; every illegal transition raises `ILLEGAL_TRANSITION`; concurrent reserve test cannot overdraw; `TransactionTestCase` used |
| B | **M2 guardian** (`core/apps/guardian`) | Pure `decide()` over typed inputs; each reason code as a function; TR39 skeleton (`confusable_homoglyphs` or equivalent maintained lib) + Damerau-Levenshtein; median/MAD anomaly; split and duplicate detection; policy loader with version; Decision canonicalisation (RFC 8785) + Ed25519 sign/verify via KeyStore port | Table-driven test per reason code and per verdict path; replay determinism (same inputs + policy_version → identical Decision bytes before signing); mypy strict |
| C | **M3 bank sandbox** (`bank_sandbox/`) | Accounts, balances, transfers idempotent on `end_to_end_id`, Ed25519-signed receipts and daily statements, failure injection switch (`/admin/fail-next`), request-signature auth | Same e2e id twice → one transfer; receipts and statements verify; injected failures produce FAILED |
| D | **M4 intel** (`intel/`) | OPRF port over `voprf` (see CLAUDE.md gotcha); epoch keys; `/v1/oprf/evaluate` with member signature + quota; report admission (member sig, bank receipt sig, payee match, payer belongs to member, ≤90 d, one count per member+payee, expiry, retraction); signed snapshots with 16-byte tags | CFRG vectors pass; forged/old/mismatched receipts rejected; quota returns 429; snapshot signature verifies |
| E | **M5 sim twins** (`sim/twins`) | 8 seeded SMEs (retail, construction, restaurant, logistics, pharmacy, agriculture, IT services, textiles), 90 days of vendors, invoices (text PDFs + email bodies), AR, opex; ground-truth labels (benign/attack/ambiguous, expected verdict, expected forecast shortfall weeks, oracle analysis answers) | Byte-identical output for the same seed; label schema validates |

**G1 passes when:** all five gates pass and `make verify` is green. **Cut line:** if G1 is not reached by 14:00, M4 shrinks to one epoch, one reporter, one tag; the rest becomes roadmap.

---

## Phase 2: Agent, money flow, humans (19 Oct 12:00-20:00) → Gate G2

| Session | Module | Build | Acceptance gate |
|---|---|---|---|
| A | **M9 bank adapter + recon** | Outbox worker (management command, SKIP LOCKED, backoff + jitter); submit/settle/fail transitions; receipt verification; reconciler (5 min + EOD) with MISSING_IN_LEDGER / AMOUNT_MISMATCH → T4 freeze, MISSING_IN_BANK → alert; balance recomputation; audit-chain verify | Chaos test: kill worker and bank mid-run, restart; books balance, no double transfer |
| B | **M6 agent** (`core/apps/agent`) | Extractor (no tools, pydantic output, provenance tags); Planner with tools `read_invoice`, `lookup_vendor`, `get_forecast`, `get_analysis`, `match_financing`, `propose_payment`, `request_human_approval`, `get_status`; deterministic mock LLM; Anthropic provider behind `SAFEPAY_LLM` | Injection corpus (≥30 cases) never produces a tool argument derived from invoice free text; payee always from registry; mock run reproducible |
| C | **M7 cashflow** (`core/apps/cashflow`) | 13-week forecast; buffer; LOW_BUFFER/SHORTFALL alerts; payables scheduler (due date; early only if discount APR > hurdle; deferral ordering); financing matcher over synthetic catalog (overdraft, invoice financing, short-term loan); analysis metrics (DPO, DSO, burn, top vendors) with source rows; feeds CASH_BUFFER_BREACH | Matches oracle on all twins; alert precision/recall computed |
| D | **M8 auth + approvals** (`accounts`, `approvals`, `tenants`) | Argon2id; django-otp TOTP; django-axes; roles owner/approver/accountant/agent; agent tokens (HMAC-stored, scoped T0-T2, TTL); step-up; 4-eyes + two approvers above threshold; freeze/unfreeze; vendor bank-change flow (owner + second person + call-back attestation); RLS middleware | Self-approval → 409 `SELF_APPROVAL`; stale OTP → `STEP_UP_REQUIRED`; agent token cannot reach config endpoints; RLS isolation test |
| E | **intel client** (`core/apps/intel_client`) | Blind/unblind via OPRF port, snapshot pull + signature check + local lookup, fail-closed `UNAVAILABLE`, report submission with bank receipt | Captured client traffic contains zero IBAN substrings |

**G2 passes when:** in docker compose, scripted flows succeed end to end: T1 auto-pay to known vendor; T2 new payee with cool-off and veto; T3 HOLD → approve with step-up; T4 freeze stops outflow; network-reported payee → HOLD.

---

## Phase 3: Evidence and console (19 Oct 20:00-20 Oct 03:00) → Gate G3

1. **M10 harness** (`sim/harness`): runs all scenarios (~60 benign, ~40 attacks in 12 classes, ~10 ambiguous; see architecture.md §5) under B0, B1, B2 with the same seeds. B0 = agent with direct transfer rights, no Guardian. Computes every metric in COMPETITION.md §4. Privacy test (adversary with transcript + key + 10^6 candidate IBANs). Latency p50/p95.
2. **Evidence bundle**: `evidence/run-<seed>/` with ops log JSONL, decisions, receipts, statements, audit chain + checkpoints, `metrics.json`; generator for `EVIDENCE.md` and PDF; `make verify-evidence` re-verifies signatures and recomputes metrics.
3. **Console** (`console/`, pnpm): approvals queue with decision explainer (reason codes, score, signed fields), forecast + alerts view, financing options, evidence/metrics page (charts from `metrics.json`), freeze button. Bundled fonts, no CDN.
4. `make verify-audit`; optional `AnchorPort` stub only if everything else is green.

**G3 passes when:** `make evidence SEED=42 && make verify-evidence` is green and all metrics come from the run.

---

## Phase 4: Deliverables and v1 submit (20 Oct 03:00-07:59)

1. `README.md`: what it is (Topic E framing), quickstart (`docker compose up`, `make demo`, `make evidence`), architecture link, results table **generated** from `metrics.json`, honest limitations, licence list.
2. `docs/SECURITY.md` (deliverable 05): tier table with links to the tests that prove each row; threat model; known-risk list (architecture.md §3.1, §7); OWASP LLM Top 10 (2025) mapping; disclosure that data is synthetic and voprf binding is unaudited.
3. Technical documentation PDF (deliverable 01) from `docs/architecture.md` + generated numbers.
4. Deck (≤10 min) and demo video (≤5 min, MP4 in repo or release; Bilibili mirror) following COMPETITION.md §7. Human records the video.
5. `make clean-clone` passes. Human submits v1 by **07:59**.

## Phase 5: Hardening (20 Oct 07:59-17:59)

Fix failures, add attack cases, improve console, re-run evidence, regenerate docs, re-submit. **Final by 17:59.** No new features after 15:00.

## Cut order (drop first → last)
1. Passkeys (django-otp-webauthn). 2. AnchorPort. 3. Financing matcher beyond 3 products. 4. Console polish. 5. Network beyond one reporter/one tag. Never cut: ledger invariants, Guardian, tiers, evidence verification, demo video, README quickstart.

## Pre-submit checklist (run before every submission)
- [ ] All six deliverables present, English, named clearly.
- [ ] Repo public and opens; README quickstart works from `make clean-clone`.
- [ ] `make verify` and `make verify-evidence` green.
- [ ] Every number in README/docs/deck matches `metrics.json` of the submitted run.
- [ ] MP4 included in the package; no required live URL; no external fonts/CDNs.
- [ ] Synthetic/simulated labels everywhere; known risks listed.
- [ ] No secrets in repo (gitleaks clean); licences listed.
- [ ] Registration form + passports sent and confirmed.

## Per-session prompt template (paste into a new Claude Code session)

```
Read CLAUDE.md, docs/architecture.md, docs/internal/BUILD_PLAN.md and the spec/ folder.
You own module <MODULE ID AND NAME> only (Phase <N>, session <X> in BUILD_PLAN.md).
Build it to its acceptance gate. Do not edit spec/ or other modules.
Write tests first. Run `make verify` until green.
Finish with: what you built, test results, any spec conflicts, and lines a human must review.
```
