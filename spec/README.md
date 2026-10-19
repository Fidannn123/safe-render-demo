# spec/: frozen contracts

Single source of truth for every module boundary. **Frozen at gate G0.** After the freeze, nothing in
this directory changes unless the human explicitly says "unfreeze spec". If a contract looks wrong,
stop and report the exact conflict instead of editing.

| File | What it fixes |
|---|---|
| `openapi-core.yaml` | Tenant-domain HTTP API (agent, humans, cash-flow tasks). |
| `openapi-intel.yaml` | Shared fraud network: VOPRF evaluate, signed snapshots, accountable reports. |
| `openapi-bank.yaml` | SIMULATED bank: transfers idempotent on `end_to_end_id`, signed receipts and statements. |
| `schemas/*.json` | JSON Schema 2020-12 payloads shared by services (`common.json` holds shared `$defs`). |
| `policy.yaml` | Guardian reason codes, weights, caps, thresholds, verdict rules. Versioned (`policy_version`). |
| `state_machine.yaml` | Payment states, allowed transitions, posting template per transition. |
| `ledger_accounts.yaml` | Chart of accounts and double-entry posting templates. |
| `errors.yaml` | Typed RFC 9457 problem codes, HTTP status, title. |
| `vectors/` | Official RFC 9497 (VOPRF), RFC 8785 (JCS) and RFC 8032 (Ed25519) test vectors. |

Cross-file invariants are enforced by `tests/spec/` (run by `make verify`), for example:
`ReasonCode` enum = policy codes; `PaymentState` enum = state machine states; `Problem.code` enum =
`errors.yaml` keys; every `x-problem-codes` entry exists; every template used by a transition exists.

Signing rule used throughout: an object's `sig` is Ed25519 over the RFC 8785 canonical JSON of the
object with `sig` removed. Hashes are lower-case hex SHA-256.
