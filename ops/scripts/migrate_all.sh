#!/bin/sh
# One-shot `migrate` compose service: the ONLY place the owner roles are used at runtime.
# Migrates each service as safepay_<svc>_owner; post_migrate then applies db_policy for safepay_<svc>_app.
set -eu
S=/run/secrets/safepay/secrets

run() {
  svc="$1"; dir="$2"; pkg="$3"
  echo "migrate: ${svc}"
  cd "/app/${dir}"
  DJANGO_SETTINGS_MODULE="${pkg}.settings.prod" \
  DB_NAME="safepay_${svc}" \
  DB_USER="safepay_${svc}_owner" \
  DB_PASSWORD_FILE="${S}/${svc}_owner_db_password" \
  DB_APP_ROLE="safepay_${svc}_app" \
  DJANGO_SECRET_KEY_FILE="${S}/${svc}_django_secret_key" \
    python manage.py migrate --noinput
}

run core core safepay_core
run intel intel safepay_intel
run bank bank_sandbox safepay_bank
echo "migrate: done"
