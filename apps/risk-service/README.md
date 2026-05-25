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

Install the repo hooks after syncing dependencies:

```bash
make hooks
```

For tests, `DATABASE_URL` can be unset. For migrations and database readiness checks, set `DATABASE_URL` to a Postgres database. For local object storage, the bundled Docker Compose file starts MinIO and creates the private `risk-local` bucket.

## Run The API

```bash
make dev
```

Local infrastructure:

```bash
docker compose up -d
make bootstrap-db
export DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service
```

`make bootstrap-db` creates separate local database roles for migrations and app
runtime, runs Alembic migrations, and grants the runtime role data privileges.
The migration role can bypass RLS for migrations and backfills; the app runtime
role is still created with `NOBYPASSRLS`.
It also seeds two demo tenants so audit foreign keys and header-based tenant
context work in local demos:

```bash
X-Org-Id: 11111111-1111-4111-8111-111111111111
X-User-Id: aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
```

Health endpoints:

- `GET /api/health/live`
- `GET /api/health/ready`

Business API endpoints use URL path major versioning under `/api/v1`. See
`docs/architecture.md` for the API versioning policy.

## Run Tests

```bash
make test
```

The default test run uses isolated SQLite databases. To run the same tests
against Postgres, start local infrastructure and provide `TEST_DATABASE_URL`.
The test fixtures create a temporary schema per fixture and drop it afterward,
so the configured database is not reset:

```bash
docker compose up -d risk-postgres
make test-postgres
```

## Lint And Type Check

```bash
make check
```

## Pre-Commit Hooks

Run all configured hooks manually:

```bash
make precommit
```

Commit messages must follow Conventional Commits. For example:

```bash
feat(risk-service): add scenario endpoint
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
# DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service

RISK_STORAGE_BACKEND=s3
RISK_S3_BUCKET=risk-local
RISK_S3_REGION=us-east-1
RISK_S3_ENDPOINT_URL=http://localhost:9000
RISK_S3_ACCESS_KEY_ID=minioadmin
RISK_S3_SECRET_ACCESS_KEY=minioadmin
RISK_S3_FORCE_PATH_STYLE=true
RISK_S3_PRESIGN_EXPIRES_SECONDS=900
RISK_MAX_UPLOAD_BYTES=25000000
```

`psycopg[binary]` is used for MVP setup convenience. Revisit production packaging before hardening deployment images.
