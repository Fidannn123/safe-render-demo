# SafePay security self-assessment

Deliverable 05 for FinTechathon 2026, Topic E. It covers the permission tiers and the tests that prove
them, the threat model, the known risks, and a mapping to the OWASP Top 10 for LLM Applications (2025).

> **All data is SYNTHETIC and the bank is SIMULATED. No real money moves.** This is a prototype. It has
> had no external penetration test and no cryptographic audit. The `voprf` 0.2 Python binding is young and
> unaudited (see Known risks).

This file contains no measured numbers. Attacker loss, attack success, false holds and latency come from
the evidence bundle: `make evidence SEED=42` writes `evidence/run-42/EVIDENCE.md` and `metrics.json`, and
`make verify-evidence SEED=42` re-checks every signature and recomputes every metric. The README
"Results" block is generated from the same run. Caps, weights and thresholds live in `spec/policy.yaml`.

## 1. How to check every claim here

| Check | Command |
|---|---|
| Unit, property, contract and DB-role tests, RFC 9497 and RFC 8032 vectors | `make verify` |
| Live walkthrough: allow, hold, injection, hijacked token, self-approval, network, freeze | `make up && make seed && make demo` |
| B0/B1/B2 attack and benign scenarios with signed records | `make evidence SEED=42` |
| Offline re-check of signatures, audit chains and metrics | `make verify-evidence SEED=42` |
| Audit hash chain of every seeded tenant from genesis | `make verify-audit` |
| The whole quickstart in a fresh copy | `make clean-clone` |

Test references below are `path::test_name`. Run one with `cd <project> && uv run pytest <path> -k <name>`.

## 2. Permission tiers

The Guardian assigns the tier. The agent cannot pick or change it, and the database enforces the parts
that matter for money. Tier rules and caps: `spec/policy.yaml`. Verdict to tier binding:
`spec/schemas/Decision.json`.

| Tier | What the agent may do | Enforced by | Proved by |
|---|---|---|---|
| T0 Read | Read balances, invoices, forecast, analysis of its own tenant | Token scope; PostgreSQL row-level security (FORCE RLS) on every tenant table | `core/tests/test_db_roles.py::test_rls_zero_rows_without_tenant`, `::test_rls_rejects_writes_for_other_tenant`, `::test_rls_is_forced_for_the_owner_too`; `core/apps/ledger/tests/test_ledger_db.py::test_rls_hides_balances_without_tenant`, `::test_posting_to_other_tenants_account_rejected` |
| T1 Auto | Pay a known registry payee under the per-payment and rolling 24 h caps with a low score | Guardian rules; caps checked under `SELECT ... FOR UPDATE` on the tenant limits row; funds reserved in the same transaction; `CHECK (balance >= 0)` | `core/apps/guardian/tests/test_decide.py::test_verdict_paths`; `core/apps/guardian/tests/test_rules.py::test_reason_code` (one case set per reason code); `core/apps/payments/tests/test_transitions.py::test_t1_happy_path_postings_audit_outbox`; `core/apps/payments/tests/test_concurrency.py::test_two_concurrent_reserves_cannot_overdraw`; `core/apps/ledger/tests/test_ledger_db.py::test_overdraft_rejected_immediately` |
| T2 Guarded | Pay a new or changed payee under the new-payee caps with a clean network result | Signed Decision; cool-off with owner veto; the outbox will not deliver during the cool-off or after a veto | `core/apps/payments/tests/test_transitions.py::test_t2_cooloff_and_veto`, `::test_veto_after_cooloff_rejected`; `core/apps/bank_adapter/tests/test_delivery.py::test_freeze_and_t2_veto_stop_bank_calls` |
| T3 Human | Nothing: the agent can only queue the payment (HOLD) | Approver is not the accountable initiator (DB `CHECK (approver_id <> initiator_id)`); step-up TOTP within 5 min; reason required; two distinct approvers at or above the two-approver threshold; hard rules, registry account, freeze and funds re-checked at approval | `core/tests/test_api_flows.py::test_bank_detail_swap_is_held_and_four_eyes_hold`; `core/apps/payments/tests/test_transitions.py::test_two_approvers_required`, `::test_stale_step_up_rejected`, `::test_warning_must_be_acknowledged`, `::test_funds_guard_on_approval`; `core/apps/guardian/tests/test_recheck.py::test_payee_changed`, `::test_frozen`, `::test_duplicate_paid_since_decision`; `tests/spec/test_openapi.py::test_step_up_operations` |
| T4 Emergency | Nothing: all outflow stops | Owner or approver freeze, or automatic freeze by the reconciler on a bank mismatch; freeze revokes every agent token; unfreeze needs the owner and a fresh step-up code | `core/tests/test_api_flows.py::test_freeze_revokes_agent_tokens_and_unfreeze_needs_owner`; `core/apps/payments/tests/test_transitions.py::test_frozen_tenant_cannot_approve_or_deliver`; `core/apps/bank_adapter/tests/test_delivery.py::test_bank_mismatch_freezes_and_incident_is_idempotent` |

Agent tokens carry only the scopes T0, T1 and T2. They cannot reach approve, reject, veto, unfreeze,
registry or configuration endpoints: `tests/spec/test_openapi.py::test_agent_cannot_reach_config_or_human_endpoints`
and `::test_proposals_only_for_t1_t2_tokens`. `make demo` steps 5 and 6 show the same refusals over HTTP
(`FORBIDDEN_SCOPE`, `SELF_APPROVAL`).

What the tiers bound if the agent is fully hijacked: T1 loss is limited to known registry payees within
the rolling caps; T2 loss is limited to the new-payee caps per payment and per rolling 24 h, and only if
the owner does not veto during the cool-off; T3 and T4 allow no agent-initiated loss.

## 3. Controls that do not depend on the LLM

| Control | Where | Proved by |
|---|---|---|
| Payee always comes from the vendor registry, never from invoice text | `core/apps/agent/service.py` `plan_payment` | `core/apps/agent/tests/test_injection.py::test_printed_iban_is_a_claim_not_the_payee`, `::test_injected_text_never_reaches_the_planner_tool` |
| Extractor output is schema-validated; raw text stays opaque and tagged `untrusted` | `core/apps/agent/extractor.py` | `core/apps/agent/tests/test_injection.py::test_raw_text_stays_opaque_and_tagged_untrusted`, `::test_model_output_with_extra_fields_is_rejected`; `tests/spec/test_schemas.py::test_untrusted_text_must_be_tagged` |
| Guardian is deterministic and its Decision is Ed25519-signed and bound to the exact payment fields | `core/apps/guardian/` | `core/apps/guardian/tests/test_decide.py::test_replay_identical_canonical_bytes`, `::test_property_deterministic_and_consistent`, `::test_tampering_fails_verification`, `::test_inputs_hash_covers_registry_account` |
| Network signals add risk but never DENY | `spec/policy.yaml`, Guardian | `core/apps/guardian/tests/test_decide.py::test_network_codes_never_deny`; `core/apps/guardian/tests/test_policy_and_detectors.py::test_policy_rejects_network_code_as_hard` |
| Intel unavailable or untrusted: fail closed for new payees | `core/apps/intel_client/` | `core/apps/intel_client/tests/test_privacy.py::test_untrusted_or_expired_response_fails_closed`, `::test_snapshot_sequence_rollback_fails_closed` |
| Journal entries balance per currency at COMMIT (deferred constraint trigger) | PostgreSQL | `core/apps/ledger/tests/test_ledger_db.py::test_unbalanced_entry_rejected_at_commit`, `::test_per_currency_balance_required`; `core/apps/payments/tests/test_properties.py::test_random_lifecycles_keep_database_invariants` (Hypothesis) |
| Ledger, audit, decision and receipt tables are INSERT-only for the app role | PostgreSQL grants and triggers | `core/tests/test_db_roles.py::test_app_cannot_mutate_append_only_rows`, `::test_app_cannot_disable_trigger`, `::test_trigger_blocks_even_the_owner`, `::test_app_role_owns_nothing` |
| No double payment | `UNIQUE(tenant, invoice_fingerprint)` over live payments; single-use Decision nonce | `core/apps/payments/tests/test_transitions.py::test_duplicate_live_fingerprint_rejected` |
| Every illegal state transition raises | `spec/state_machine.yaml`, `core/apps/payments/` | `core/apps/payments/tests/test_illegal_transitions.py::test_illegal_pair_raises`, `::test_terminal_states_have_no_exit` |
| Idempotent API writes | Idempotency-Key stored with request hash and response in the same transaction | `core/apps/payments/tests/test_idempotency.py::test_different_body_is_mismatch`, `::test_handler_failure_rolls_back_key` |
| Settlement only with a valid bank signature; bank idempotent on `end_to_end_id` | `core/apps/bank_adapter/`, `bank_sandbox/` | `core/apps/bank_adapter/tests/test_delivery.py::test_forged_receipt_cannot_settle`, `::test_timeout_after_bank_commit_retries_exact_request_once`; `bank_sandbox/tests/test_transfers.py::test_same_e2e_different_body_is_409` |
| Tamper-evident audit log (SHA-256 chain, RFC 8785 JSON, Ed25519 checkpoints) | `core/apps/audit/` | `core/apps/audit/tests/test_chain.py::test_tampered_event_detected`, `::test_verify_detects_forged_hash_inserted_by_owner`; `core/apps/audit/tests/test_checkpoints.py::test_checkpoint_links_and_tampering`; `make verify-audit` |
| Passwords hashed with Argon2id first, PBKDF2 only to upgrade | settings of all three services | `core/tests/test_boot.py::test_argon2_is_first_and_pbkdf2_only_second`, `::test_new_passwords_use_argon2id` |
| Service-to-service requests are Ed25519-signed with timestamp and nonce | `intel/`, `bank_sandbox/` | `intel/tests/test_request_signing.py::test_nonce_replay_rejected`, `::test_body_is_signed`, `::test_timestamp_outside_skew_rejected`; `bank_sandbox/tests/test_signing.py::test_request_signature_binds_every_component` |

## 4. Fraud network: what it proves and what it does not

| Property | Proved by |
|---|---|
| The VOPRF wrapper matches the CFRG RFC 9497 vectors (ristretto255-SHA512, VOPRF mode) | `intel/tests/test_oprf_vectors.py` (all tests); `tests/spec/test_errors_and_vectors.py` |
| A bad or swapped proof is rejected | `intel/tests/test_oprf_vectors.py::test_tampered_proof_is_rejected`, `::test_proof_from_other_key_is_rejected`, `::test_swapped_elements_are_rejected` |
| Blind queries never carry the IBAN or the tag | `core/apps/intel_client/tests/test_privacy.py::test_blind_hit_and_miss_never_send_identifier_or_tag`; the evidence run searches every captured blind query for the twins' IBANs (`evidence/run-42/privacy.json`, and `EVIDENCE.md`, "Fraud network") |
| The snapshot holds tags only, no payee identifiers | `intel/tests/test_snapshots.py::test_snapshot_reveals_no_payee_identifier` |
| Enumeration is bounded by a per-member quota | `intel/tests/test_oprf_api.py::test_quota_exceeded_returns_429`, `::test_quota_counts_each_blinded_element_and_is_per_member`; harness class OPRF_ENUMERATION |
| A report needs a bank-signed receipt proving the reporter paid that account | `intel/tests/test_reports.py::test_forged_receipt_signature_rejected`, `::test_receipt_paid_by_another_member_rejected`, `::test_payee_mismatch_rejected`, `::test_old_receipt_rejected` |
| One count per member and payee; expiry; retraction only by the reporter | `intel/tests/test_reports.py::test_one_active_report_per_member_and_payee`, `::test_expired_report_no_longer_blocks_and_reports_expired`, `::test_retraction_by_non_reporter_forbidden` |

Not proved: the stronger privacy test in `docs/architecture.md` section 2.7 (an adversary holding the
server transcript, the server key and a large candidate IBAN list) is not implemented. What is measured
is that no captured query contains an IBAN and that the server only ever sees blinded group elements.

## 5. Threat model

Trust domains: untrusted (invoices, vendor change requests, LLM output, the browser), tenant domain (core
and its database), shared intel network (separate service and database), and the simulated bank. Diagram
and component worst cases: `docs/architecture.md` section 1.

| # | Threat | Control | Evidence | Residual |
|---|---|---|---|---|
| 1 | Hidden instructions in an invoice | Quarantined extractor with no tools; planner sees typed fields only; payee from registry; deterministic Guardian; INJECTION_MARKERS signal | Section 3; harness class HIDDEN_INSTRUCTION | A wrong but plausible amount is bounded by caps and AMOUNT_ANOMALY |
| 2 | Bank detail change by email (invoice redirection) | Payee from registry; BANK_DETAIL_MISMATCH forces HOLD; new account is T2 or T3 with a network check | `core/tests/test_api_flows.py::test_bank_detail_swap_is_held_and_four_eyes_hold`; class BANK_DETAIL_SWAP | Social engineering of the staff who edit the registry |
| 3 | Look-alike vendor or account | TR39 skeleton plus Damerau-Levenshtein; account prefix and suffix match; new-payee caps | `core/apps/guardian/tests/test_policy_and_detectors.py::test_names_look_alike`, `::test_accounts_look_alike`; class LOOKALIKE_VENDOR_OR_ACCOUNT | Heuristic false negatives |
| 4 | Hijacked planner (excessive agency) | One consequential tool (`propose_payment`); tiers enforced server-side and in PostgreSQL | Section 2 | Bounded by T1 and T2 caps |
| 5 | Stolen agent token | Scopes T0 to T2 only; at most 1 h TTL; stored as HMAC-SHA256(pepper, token) (`core/apps/accounts/service.py`); revoked by freeze | Class STOLEN_TOKEN_DIRECT_API; `make demo` steps 5 and 11 | Loss up to the rolling caps until someone freezes |
| 6 | Insider self-approval | Accountable initiator is the human who issued the token (D-036); DB CHECK approver <> initiator; step-up; two approvers above the threshold | `core/tests/test_api_flows.py::test_bank_detail_swap_is_held_and_four_eyes_hold`; class SELF_APPROVAL | Collusion of two staff |
| 7 | Double spend or race | Row lock on limits; UNIQUE fingerprint and nonce; `CHECK (balance >= 0)` | `core/apps/payments/tests/test_concurrency.py`; class CONCURRENT_DOUBLE_SUBMIT, DUPLICATE_OR_NEAR_DUPLICATE | None known |
| 8 | Network poisoning by false reports | Bank-attested proof of payment; distinct reporters; expiry; never DENY | Section 4; class FALSE_REPORT_POISONING | Coordinated members can force HOLDs on an honest payee |
| 9 | Curious network operator | VOPRF blind queries; local snapshot lookup | Section 4 | Operator sees member, time and query count |
| 10 | Database exfiltration | IBANs encrypted with AES-256-GCM (AAD = tenant id); Argon2id passwords; HMAC-hashed tokens; RLS | `core/apps/vendors/models.py` | Key file on the same host as the data (see Known risks) |
| 11 | Audit tampering or replay | Hash chain; signed checkpoints; single-use, field-bound, expiring Decisions; signed receipts | Section 3; `make verify-audit` | A key holder could rewrite events after the last exported checkpoint |
| 12 | Paying into a cash shortfall | CASH_BUFFER_BREACH uses the same 13-week forecast; at approval it is a warning the approver must acknowledge | `core/apps/guardian/tests/test_recheck.py::test_cash_buffer_warning_needs_ack`; class CASH_BUFFER_BREACH | Forecast error on unusual weeks |
| 13 | Supply chain | `uv.lock` and `pnpm-lock.yaml` pinned and installed frozen; pip-audit, pnpm audit and gitleaks in CI; bundled console assets, no CDN | `.github/workflows/ci.yml` | Zero-days in pinned dependencies |

## 6. OWASP Top 10 for LLM Applications (2025)

| ID | Risk | SafePay position |
|---|---|---|
| LLM01 | Prompt Injection | Extractor has no tools and its output is a pydantic model with `extra="forbid"`; raw text is tagged `untrusted` and dropped before the planner; payee comes from the registry. Tests: `core/apps/agent/tests/test_injection.py`. Harness: HIDDEN_INSTRUCTION. |
| LLM02 | Sensitive Information Disclosure | The LLM receives only the invoice it extracts: no registry, credentials or other tenants. IBANs are masked in API responses and encrypted at rest. The network never receives a queried IBAN. |
| LLM03 | Supply Chain | Locked dependencies, CI audits and secret scanning; the real model id is pinned via `SAFEPAY_LLM_MODEL`; the default is a local deterministic mock. |
| LLM04 | Data and Model Poisoning | No training or fine-tuning. The analogous risk, poisoned shared fraud data, is handled by bank-attested reports and the never-DENY rule (section 4). |
| LLM05 | Improper Output Handling | Model output is parsed only as JSON against a strict schema; server-owned fields (`invoice_id`, provenance, extractor) are overwritten after parsing; no output is executed or rendered as HTML. |
| LLM06 | Excessive Agency | One consequential tool, `propose_payment`; agent tokens limited to T0 to T2; human-only endpoints refuse agent tokens; caps and tiers enforced in PostgreSQL. Section 2. |
| LLM07 | System Prompt Leakage | The prompt holds no secrets or rules; every rule lives in `spec/policy.yaml` and the Guardian, so a leaked prompt gives an attacker nothing to bypass. |
| LLM08 | Vector and Embedding Weaknesses | Not applicable: no retrieval, vector store or embeddings. |
| LLM09 | Misinformation | Every number the agent shows comes from a deterministic function (forecast, analysis with source rows, financing match). The LLM only phrases. |
| LLM10 | Unbounded Consumption | Invoice size limits (100 PDF pages, 200,000 characters), `max_tokens` on the provider call, OPRF quota per member per day. |

## 7. Known risks

| Risk | Status |
|---|---|
| `voprf` 0.2 binding is young and unaudited (the Rust core is Meta's) | Pinned; wrapped behind `intel/apps/oprf/port.py`; CFRG vectors in CI. This is not "audited cryptography". |
| T2 residual loss | A new-payee attack that stays under the new-payee caps and scores below the HOLD threshold is paid after the cool-off unless the owner vetoes. The harness never vetoes, so every such case is reported as residual loss, per scenario, in `EVIDENCE.md` ("Attacks that still moved money"). Mitigations: lower new-payee caps, longer cool-off, or route all new payees to T3. |
| Single OPRF key holder | VOPRF stops per-client keys, not operator collusion or downtime. Threshold OPRF is roadmap. |
| Coordinated false reports | Can force HOLDs, never losses. A dispute and suspension path exists for reporters. |
| Keys on the same host as the data | Prototype only. All keys are files under `ops/dev-keys/` or the core volume. The KeyStore port is meant to map to a KMS or HSM. |
| PII encryption is simpler than the design | One AES-256-GCM key per deployment with the tenant id as AAD, not a per-tenant data key wrapped by a KEK as in `docs/architecture.md` section 3.3. |
| No automatic velocity freeze | The automatic T4 freeze comes from the reconciler on a bank mismatch. A freeze on unusual agent velocity is not built; caps still bound the loss. |
| Stronger privacy test not built | See section 4. |
| Real LLM not measured | All tests and the evidence run use the deterministic mock. `SAFEPAY_LLM=anthropic` works but has no measured injection results. |
| Scripted attackers | Attack classes are scripted scenarios, not adaptive adversaries. |
| Synthetic data and simulated bank | Disclosed on every artifact. A real pilot is the next step. |
| No external review | No penetration test, cryptographic audit or legal review of cross-business data sharing. |

## 8. Disclosure

All companies, invoices, accounts and amounts are synthetic (`sim/safepay_sim/twins`). The bank is a
simulation (`bank_sandbox/`). No component moves real money or touches any blockchain.
