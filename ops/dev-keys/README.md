# ops/dev-keys/ (generated, gitignored)

Everything here except this README is produced by `ops/scripts/gen_dev_keys.py` (via `make keys`, or
the compose `keygen` service on `make up`) and is **never committed**. DEV/DEMO ONLY: production keys
live in KMS/HSM behind the KeyStore port. Rotate with `uv run python ops/scripts/gen_dev_keys.py --force`
followed by `make down-volumes` (DB passwords change).
