# AequorOS Risk Service

The risk service is the backend API for AequorOS risk workflows. It owns the server-side contracts and persistence for liquidity risk, Basel capital, balance sheet forecasting, data ingestion, scenario runs, audit trails, and report generation.

The service is built with FastAPI, Pydantic settings, SQLAlchemy, Alembic, and Postgres. It is designed to keep route handlers thin, isolate database setup, centralize configuration, and provide consistent request tracing and error responses.

## Current Surface

The service currently provides the backend foundation, financial-data APIs,
case scenario management, deterministic balance-sheet forecasts, and the first
capital projection workflow:

- Health and readiness endpoints under `/api/health`
- Centralized environment-based settings
- Request ID propagation through `X-Request-ID`
- Loguru JSON structured logging
- Consistent API error envelopes
- SQLAlchemy models and Alembic migrations for tenant-owned risk and financial records
- Canonical financial-workspace mapping, validation, manual entry, and correction
- Covenant persistence, mapping, deterministic compliance validation, and correction
- Tenant-scoped baseline, downside, and custom scenarios with structured assumptions
- Scenario creation, editing, copying, archiving, review, validation, and calculation readiness
- Tenant-scoped calculation runs with immutable input snapshots, version metadata, and audit events
- Deterministic annual balance-sheet forecasts, persisted failures, reruns, and paginated history
- Tenant-scoped capital projection attempts with period pressure indicators and persisted diagnostics
- Latest capital summaries, baseline-versus-downside comparisons, and generated findings with evidence
- Versioned liquidity metrics and severity-ranked findings generated from successful forecasts
- Liquidity finding evidence, acknowledge/dismiss review actions, and audit events
- Audit events, per-field manual edit history, and source-record traceability

Regulatory LCR/NSFR and Basel regulatory-capital scoring, full ingestion pipelines, auth,
background workers, advanced forecast configuration, and report generation are
intentionally not implemented yet.

## Requirements

- Python 3.13
- uv
- mise
- Postgres for migrations and database-backed readiness checks

## Local Setup

From `apps/risk-service`:

```bash
mise trust
uv sync
cp .env.example .env
```

Or use the task runner:

```bash
mise run risk-service:sync
```

The same `risk-service:*` tasks are also available from the repository root.

Install the repo hooks after syncing dependencies:

```bash
mise run risk-service:hooks
```

For tests, `DATABASE_URL` can be unset. For migrations and database readiness checks, set `DATABASE_URL` to a Postgres database. For local object storage, the bundled Docker Compose file starts MinIO and creates the private `risk-local` bucket.

## Run The API

```bash
mise run risk-service:dev
```

Local infrastructure:

```bash
docker compose up -d
mise run risk-service:bootstrap-db
export DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service
```

`mise run risk-service:bootstrap-db` creates separate local database roles for migrations and app
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

Canonical financial data is read with
`GET /api/v1/cases/{case_id}/financial-workspace`. Resource-specific `POST` and
`PATCH` routes below that path support institutions, accounts, reporting
periods, balances, cash flows, obligations, and covenants. These mutations
require both `X-Org-Id` and `X-User-Id`; each request body requires a non-empty
`reason`. Successful responses contain the updated `record` and the case's
refreshed `validation` state. See `docs/architecture.md` for the complete
contract and correction-history behavior.

Case scenarios are read from `GET /api/v1/cases/{case_id}/scenarios`. Initialize
the baseline and downside defaults with `POST .../scenarios/initialize`, or use
the resource-specific scenario, copy, archive, assumption, and review routes
below that path. All mutations require `X-Org-Id`, `X-User-Id`, and a non-empty
`reason`. An active scenario is calculation-ready only when it has a non-null,
reviewed assumption in each required category: growth, expenses, cash-flow
timing, credit usage, and repayment behavior. Editing or copying an assumption
resets its review state to `draft`. Mutation responses include the scenario's
refreshed validation and the case's refreshed readiness state.

Balance-sheet forecast attempts use
`/api/v1/cases/{case_id}/calculation-runs`:

```text
GET  /api/v1/cases/{case_id}/calculation-runs
POST /api/v1/cases/{case_id}/calculation-runs
GET  /api/v1/cases/{case_id}/calculation-runs/{run_id}
POST /api/v1/cases/{case_id}/calculation-runs/{run_id}/rerun
```

Starting a run requires `scenario_id`, accepts one to twelve annual
`forecast_periods` (default three), and optionally accepts `as_of_date`, which
defaults to today.
Rerunning creates a new run for the original scenario using current canonical
financial data and reviewed assumptions. Its body may be `{}`; omitted fields
reuse the original period count and default the as-of date to today, while
provided fields override those values. Both mutations require `X-Org-Id` and
`X-User-Id`.

The first engine executes synchronously, but commits its `queued` and `running`
states before calculation. A `201` response contains the final persisted run,
including a `failed` run with actionable diagnostics. Successful output and
failed diagnostics remain immutable history. List requests support optional
`scenario_id`, `limit` (1-100), and `offset`; summaries omit the full input and
output payloads, which are available from the run detail route. Setting
`active_scenarios_only=true` excludes archived scenarios and also returns the
latest successful run per active scenario, paginated by the same `limit` and
`offset`; this supports downstream capital-run selection without losing older
attempt history from the main `runs` list.

Forecast snapshots use the newest effective balance date on or before the
requested as-of date and the matching reporting-period cash flows and active
obligations. All selected inputs must use one currency; active obligations need
principal and outstanding amounts. The selected scenario must have reviewed,
unambiguous values for all five required assumption categories.

Capital projection attempts consume an immutable successful forecast run:

```text
GET  /api/v1/cases/{case_id}/capital-projections
POST /api/v1/cases/{case_id}/capital-projections
GET  /api/v1/cases/{case_id}/capital-projections/{projection_id}
GET  /api/v1/cases/{case_id}/capital-summary
GET  /api/v1/cases/{case_id}/capital-comparison
```

Creating a projection requires `calculation_run_id`, `X-Org-Id`, and
`X-User-Id`. The run must be successful and belong to an active scenario in the
same case and tenant. Each attempt is immutable and stores the run input hash,
engine version, reporting currency, lifecycle state, period indicators, and any
named failure diagnostic. The history route is newest-first and supports
`limit` (1-100) and `offset`; the summary route returns the latest successful
projection, optionally filtered by `scenario_id`.

Indicators derive equity, equity-to-assets, liabilities-to-assets, equity
change, and a deterministic pressure level from the forecast periods. Monetary
values are persisted to four decimal places and ratios are rounded half-up to
eight decimal places before pressure classification and finding generation.
Generated capital findings include evidence linking the projection, calculation
run, scenario, input hash, indicator, and forecast period. A newer successful
projection supersedes only unreviewed findings for the same scenario. The
comparison route pairs the latest successful active baseline and downside
projections; mismatched as-of dates, currencies, or horizons return a
diagnostic instead of period deltas. Non-positive projected assets and missing
or out-of-range forecast evidence persist the attempt as failed with corrective
details.

Projection list, detail, and summary reads retain historical attempts after a
scenario or case is archived. Archived scenarios cannot start new projections,
and an archived case also rejects new projections, comparisons, and finding
reviews. Comparisons exclude archived scenarios.

Every successful forecast also persists a versioned liquidity analysis and
publishes deterministic findings for the same immutable run. Read either the
latest successful run, or select a scenario and run explicitly, with:

```text
GET /api/v1/cases/{case_id}/liquidity/summary?scenario_id={scenario_id}&run_id={run_id}
```

The summary reports minimum cash, peak liquidity gap, minimum sources coverage,
credit reliance, and cash runway. A metric is returned as unavailable with an
explicit diagnostic when its denominator is not positive. Findings are ordered
by severity and include links to forecast periods, canonical inputs, and
reviewed scenario assumptions, all bound to the calculation input hash.

Review an open liquidity finding with:

```text
POST /api/v1/cases/{case_id}/liquidity/findings/{finding_id}/review
```

The body action is `acknowledge` or `dismiss`; dismissal requires a non-empty
reason. Review requires `X-Org-Id` and `X-User-Id`, records audit events, and is
rejected for terminal findings or findings belonging to archived scenarios.
The generic findings update endpoint does not mutate liquidity workflow
findings. A newer successful run supersedes open findings from the previous run
for that scenario without altering acknowledged or dismissed history.

## Run Tests

```bash
mise run risk-service:test
```

The default test run uses isolated SQLite databases. To run the same tests
against Postgres, start local infrastructure and provide `TEST_DATABASE_URL`.
The test fixtures create a temporary schema per fixture and drop it afterward,
so the configured database is not reset:

```bash
docker compose up -d risk-postgres
mise run risk-service:test-postgres
```

## Lint And Type Check

```bash
mise run risk-service:check
```

## Pre-Commit Hooks

Run all configured hooks manually:

```bash
mise run risk-service:precommit
```

Commit messages must follow Conventional Commits. For example:

```bash
feat(risk-service): add scenario endpoint
```

## Run Migrations

`DATABASE_URL` is required for migrations.

```bash
mise run risk-service:migrate
```

To create a migration revision:

```bash
mise run risk-service:revision "describe change"
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
