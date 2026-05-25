# AequorOS Risk Service

The risk service is the backend API for AequorOS risk workflows. It owns the server-side contracts and persistence for liquidity risk, Basel capital, balance sheet forecasting, data ingestion, scenario runs, audit trails, and report generation.

The service is built with FastAPI, Pydantic settings, SQLAlchemy, Alembic, and Postgres. It is designed to keep route handlers thin, isolate database setup, centralize configuration, and provide consistent request tracing and error responses.

## Current Surface

The service currently provides the backend foundation:

- Health and readiness endpoints under `/api/health`
- Centralized environment-based settings
- Request ID propagation through `X-Request-ID`
- Loguru JSON structured logging
- Consistent API error envelopes
- SQLAlchemy models and Alembic migration setup for organizations, users, and audit events
- Placeholder domain packages for risk workflows

Risk calculations, ingestion pipelines, auth, background workers, and report generation are intentionally not implemented yet.

## Requirements

- Python 3.13
- uv
- Postgres for migrations and database-backed readiness checks

## Local Setup

From `apps/risk-service`:

```bash
uv sync
cp .env.example .env
```

Or use the task runner:

```bash
make sync
```

For tests, `DATABASE_URL` can be unset. For migrations and database readiness checks, set `DATABASE_URL` to a Postgres database.

## Run The API

```bash
make dev
```

Health endpoints:

- `GET /api/health/live`
- `GET /api/health/ready`

## Run Tests

```bash
make test
```

## Lint And Type Check

```bash
make check
```

## Run Migrations

`DATABASE_URL` is required for migrations.

```bash
make migrate
```

To create a migration revision:

```bash
make revision name="describe change"
```

## Environment Variables

```bash
APP_ENV=local
APP_NAME=risk-service
CORS_ORIGINS=http://localhost:3000,http://localhost:3001
LOG_LEVEL=INFO

# Required for migrations and database-backed readiness checks.
# DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/risk_service
```

`psycopg[binary]` is used for MVP setup convenience. Revisit production packaging before hardening deployment images.
