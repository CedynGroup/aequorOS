# syntax=docker/dockerfile:1.7

# Pinned for reproducible builds. Refresh this digest during routine security patching.
ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:e4fa1f978c539608a10cdf74700ac32a3f719dfc6e8b6b6001da82deb36302a2

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

ENV APP_ENV=local \
    APP_NAME=risk-service \
    LOG_LEVEL=INFO \
    PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN groupadd --system risk \
    && useradd --system --gid risk --home-dir /app --shell /usr/sbin/nologin risk

COPY --from=builder --chown=risk:risk /opt/venv /opt/venv
COPY --chown=risk:risk app ./app
COPY --chown=risk:risk alembic ./alembic
COPY --chown=risk:risk alembic.ini pyproject.toml uv.lock ./

USER risk

EXPOSE 8000

CMD ["fastapi", "run", "app/main.py", "--host", "0.0.0.0", "--port", "8000"]
