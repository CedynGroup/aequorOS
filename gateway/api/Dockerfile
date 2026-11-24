# AequorOS Reporting Gateway — API service image. Build context: gateway/api.
# Pinned for reproducible builds. Refresh this digest during routine security patching.
ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.13-slim-bookworm@sha256:e4fa1f978c539608a10cdf74700ac32a3f719dfc6e8b6b6001da82deb36302a2

FROM ${PYTHON_IMAGE} AS builder

ENV PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /app

RUN python -m pip install --no-cache-dir "uv==0.11.16"

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

FROM ${PYTHON_IMAGE} AS runtime

# The keys directory is a volume (docker-compose.prod.yml): the receipt and audit
# keys and the client-certificate CA outlive every image.
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GATEWAY_KEYS_DIR=/var/lib/gateway/keys

WORKDIR /app

RUN groupadd --system gateway \
    && useradd --system --gid gateway --home-dir /app --shell /usr/sbin/nologin gateway \
    && mkdir -p /var/lib/gateway/keys \
    && chown -R gateway:gateway /var/lib/gateway \
    && chmod 700 /var/lib/gateway/keys

COPY --from=builder --chown=gateway:gateway /opt/venv /opt/venv
COPY --chown=gateway:gateway app ./app
COPY --chown=gateway:gateway alembic ./alembic
COPY --chown=gateway:gateway alembic.ini pyproject.toml uv.lock ./

USER gateway

EXPOSE 8200

# ONE process: the scheduler (audit-chain verification, validation sweep) runs inside
# it, so a second worker would run every job twice. Proxy headers are NOT honoured:
# the client address must stay the ingress's, because the client-certificate header
# is trusted only from GATEWAY_TRUSTED_PROXIES.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8200", "--workers", "1", "--no-proxy-headers", "--no-server-header"]
