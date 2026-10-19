#!/bin/sh
# Runs once on first start of an empty data directory (docker-entrypoint-initdb.d).
# Per service (DECISIONS.md D-013): safepay_<svc>_owner owns the database and schema and is used ONLY by
# the one-shot `migrate` service; safepay_<svc>_app is the runtime role (NOSUPERUSER, NOBYPASSRLS, owns
# nothing). Table grants, append-only triggers and FORCE RLS are applied by db_policy.py after migrate.
# Owners have CREATEDB only so Django's test runner can create test_<db> (dev/CI).
set -eu
SECRETS_DIR="${SAFEPAY_SECRETS_DIR:-/run/secrets/safepay/secrets}"

for svc in core intel bank; do
  opw="$(cat "$SECRETS_DIR/${svc}_owner_db_password")"
  apw="$(cat "$SECRETS_DIR/${svc}_app_db_password")"
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -v opw="$opw" -v apw="$apw" <<SQL
CREATE ROLE safepay_${svc}_owner LOGIN CREATEDB NOSUPERUSER NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'opw';
CREATE ROLE safepay_${svc}_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT PASSWORD :'apw';
CREATE DATABASE safepay_${svc} OWNER safepay_${svc}_owner;
REVOKE ALL ON DATABASE safepay_${svc} FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE safepay_${svc} TO safepay_${svc}_owner;
GRANT CONNECT ON DATABASE safepay_${svc} TO safepay_${svc}_app;
SQL
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "safepay_${svc}" <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO safepay_${svc}_owner;
GRANT USAGE ON SCHEMA public TO safepay_${svc}_app;
SQL
done

# Maintenance DB: only owners (Django test runner); app roles never connect to it.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
REVOKE CONNECT ON DATABASE postgres FROM PUBLIC;
GRANT CONNECT ON DATABASE postgres TO safepay_core_owner, safepay_intel_owner, safepay_bank_owner;
SQL
echo "safepay: created databases, owner roles and app roles for core, intel, bank"
