# One disposable web service for the SYNTHETIC demo, not production packaging.
FROM node:22.23.3-alpine AS console
RUN corepack enable
WORKDIR /repo
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY console/package.json console/
COPY console/packages/api-client/package.json console/packages/api-client/
RUN pnpm install --frozen-lockfile
COPY spec/ spec/
COPY console/ console/
RUN pnpm run generate && pnpm --filter @safepay/console run build

FROM python:3.13.16-slim-bookworm AS python
ENV UV_PROJECT_ENVIRONMENT=/opt/venv UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.9.17
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY core/pyproject.toml core/
COPY intel/pyproject.toml intel/
COPY bank_sandbox/pyproject.toml bank_sandbox/
COPY sim/pyproject.toml sim/
RUN uv sync --frozen --no-dev --all-packages

FROM postgres:18.6-bookworm
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    nginx libbz2-1.0 libexpat1 libffi8 libgdbm6 libgdbm-compat4 liblzma5 \
    libncursesw6 libreadline8 libsqlite3-0 libuuid1 ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=python /usr/local/ /usr/local/
COPY --from=python /opt/venv/ /opt/venv/
RUN ldconfig
ENV PATH=/opt/venv/bin:/usr/local/bin:$PATH \
    PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    SAFEPAY_SPEC_DIR=/app/spec \
    SAFEPAY_KEYS_DIR=/tmp/safepay/keys \
    SAFEPAY_NATIVE_HOME=/tmp/safepay \
    SAFEPAY_PG_PORT=55432 \
    SAFEPAY_RUNNER=native
WORKDIR /app
COPY --chown=postgres:postgres spec/ spec/
COPY --chown=postgres:postgres core/ core/
COPY --chown=postgres:postgres intel/ intel/
COPY --chown=postgres:postgres bank_sandbox/ bank_sandbox/
COPY --chown=postgres:postgres sim/ sim/
COPY --chown=postgres:postgres ops/scripts/ ops/scripts/
COPY --chown=postgres:postgres ops/render/ ops/render/
COPY --from=console /repo/console/dist/ /srv/console/
RUN mkdir -p /app/evidence /tmp/safepay \
    && ln -s /tmp/safepay/keys /app/ops/dev-keys \
    && chown -R postgres:postgres /app/evidence /tmp/safepay
USER postgres
EXPOSE 10000
ENTRYPOINT ["python", "/app/ops/render/start.py"]
