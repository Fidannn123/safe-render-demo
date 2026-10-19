# evidence/ (GENERATED ONLY)

Everything here is produced by `make evidence SEED=<n>` (M10 harness) into `evidence/run-<seed>/` and
re-checked by `make verify-evidence`. Never write files here by hand; only this README and `schema/`
are tracked. All data is SYNTHETIC and the bank is SIMULATED.

Bundle contents (Phase 3): `ops.jsonl` (one `spec/schemas/EvidenceRecord.json` per line), decisions,
bank-signed receipts and statements, audit chain + checkpoints, `metrics.json`, generated
`EVIDENCE.md` and PDF.
