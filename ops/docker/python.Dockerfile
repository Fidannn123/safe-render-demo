# One image for every Python service (core-web, core-worker, intel, bank, keygen).
# Dependencies come from uv.lock only (--frozen); sources are copied after for layer caching.
FROM python:3.13.16-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN pip install --no-cache-dir uv==0.9.17

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY intel/pyproject.toml intel/
COPY bank_sandbox/pyproject.toml bank_sandbox/
COPY sim/pyproject.toml sim/
RUN uv sync --frozen --no-dev --all-packages

COPY spec/ spec/
COPY ops/scripts/ ops/scripts/
COPY core/ core/
COPY intel/ intel/
COPY bank_sandbox/ bank_sandbox/
COPY sim/ sim/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin safepay \
    && mkdir -p /var/lib/safepay \
    && chown safepay:safepay /var/lib/safepay
USER 10001
ENV SAFEPAY_SPEC_DIR=/app/spec \
    SAFEPAY_KEYS_DIR=/run/secrets/safepay
