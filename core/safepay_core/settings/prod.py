"""Container settings (docker compose). TLS terminates at the edge in production."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .base import *

if not SECRET_KEY:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY or DJANGO_SECRET_KEY_FILE must be set")
if not DATABASES["default"]["PASSWORD"]:
    raise ImproperlyConfigured("DB_PASSWORD or DB_PASSWORD_FILE must be set")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env("SAFEPAY_SSL_REDIRECT", "0") == "1"
SECURE_HSTS_SECONDS = int(env("SAFEPAY_HSTS_SECONDS", "31536000"))
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
CSRF_TRUSTED_ORIGINS = [
    o for o in env("DJANGO_CSRF_TRUSTED_ORIGINS", "http://localhost:8080").split(",") if o
]
