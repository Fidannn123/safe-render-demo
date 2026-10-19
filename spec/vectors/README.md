# spec/vectors/: official test vectors

Downloaded once at G0 by `ops/scripts/fetch_vectors.py` from pinned official sources. The SHA-256 of
every downloaded file is in `SOURCES.sha256`; `tests/spec/test_vectors.py` re-checks those hashes.

| Path | Standard | Source (pinned) | Used by |
|---|---|---|---|
| `rfc9497/allVectors.json` | RFC 9497 OPRF/VOPRF/POPRF, all suites | CFRG draft repo `cfrg/draft-irtf-cfrg-voprf`, `poc/vectors/allVectors.json` at commit `43da59f797c92248c0362e4c74cc10a8bd2b7b7d` (the generator of RFC 9497 Appendix A) | M4 `intel/apps/oprf/port.py`: must pass the `ristretto255-SHA512`, `mode: 1` (VOPRF) entries before anything else uses OPRF |
| `rfc8785/{input,output}/*.json` | RFC 8785 JSON Canonicalization Scheme | `cyberphone/json-canonicalization` `testdata/` at commit `32a48195b9b20fe67b1795e39e4509f14d860b45` (reference implementation cited by RFC 8785) | M2 Decision canonicalisation, audit hash chain, all signatures |
| `ed25519/rfc8032.json` | RFC 8032 section 7.1 Ed25519 | Parsed from https://www.rfc-editor.org/rfc/rfc8032.txt (hash of the RFC text recorded) | KeyStore / signing wrappers |

In `allVectors.json`, `mode` is 0 = OPRF, 1 = VOPRF, 2 = POPRF. The VOPRF ristretto255 entry holds
`skSm`, `pkSm`, `seed`, `keyInfo` and vectors with `Input`, `Blind`, `BlindedElement`,
`EvaluationElement`, `Proof` (`{proof, r}`), `Output`, and `Batch` (inputs joined by `,` when > 1).

Do not edit these files. To refresh (only after "unfreeze spec"): `python -I ops/scripts/fetch_vectors.py .`
