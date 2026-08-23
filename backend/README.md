# AequorOS Risk Service

The risk service is the backend API for AequorOS risk workflows. It owns the server-side contracts and persistence for liquidity risk, Basel capital, balance sheet forecasting, data ingestion, scenario runs, audit trails, and report generation.

The service is built with FastAPI, Pydantic settings, SQLAlchemy, Alembic, and Postgres. It is designed to keep route handlers thin, isolate database setup, centralize configuration, and provide consistent request tracing and error responses.

## Current Surface

The service owns the server-side contracts and persistence for the six
regulatory modules, the Data Engine, the regulatory-reporting spine, and the
attestation/e-signature ceremony. It has three entrypoints: the tenant API
(`app.main:app`, :8000), the background worker (`python -m app.worker`), and the
staff operator control plane (`app.operator.main:app`, :8100 — never mounted on
the tenant API).

- Health and readiness under `/api/health`; readiness reports database, storage,
  worker and signing subsystems independently
- Password and OIDC SSO authentication (AequorOS is its own relying party — no
  third-party broker), integration-key service accounts, RLS-forced tenancy
- Data Engine: Excel/CSV upload, push API, market-data adapters, and the
  database-direct adapter (see the deployment note below)
- Six calculation modules — liquidity (LCR/NSFR/stress/LMT), Basel capital
  (RWA/CAR/stress), IRRBB, FX, FTP, and balance-sheet forecasting — each a pure
  Decimal engine under `app/domain/` behind an immutable `RegulatoryRun`
- Live engine: debounced `pipeline_refresh` jobs re-derive facts and update
  `live_metrics`/`live_findings`; `official_run` jobs mint the immutable filing runs
- Regulatory reporting: BoG BSD returns generated from the official workbook
  templates, with the templates' own formulas evaluated; PDF/XLSX artifacts
- Attestation: maker-checker plus PDF e-signature with step-up re-authentication
- Cash-flow LSTM (`app/ml`) and per-tenant behavioral GBMs; everything else is
  deterministic
- Audit events, per-field manual edit history, and source-record traceability

### Deployment note — database-direct drivers

`Dockerfile` runs `uv sync --locked --no-dev`, which does **not** install the
`db-direct` extra. The Oracle thin driver (`oracledb`) is a core dependency and
therefore ships; **`pyodbc` (SQL Server/ODBC), `jaydebeapi`/`JPype1` (generic
JDBC) and `snowflake-connector-python` do not.** Those backends are implemented
and covered by tests, and they fail closed at runtime with a classified
`DRIVER_UNAVAILABLE`, but they cannot connect from the default image. `pyodbc`
additionally needs `unixodbc-dev` plus a vendor ODBC driver, and the JDBC path
needs a JRE — enabling them is an image and licensing decision, not a flag.

### Not implemented

Transmission of returns to the Bank of Ghana (ORASS) is not built; the platform
generates, validates, certifies and exports, and a human files. Live vendor
market-data transports (Bloomberg, LSEG) are fixture-driven only. See
`docs/audit/15_known_limitations.md`.

## Requirements

- Python 3.13
- uv
- mise
- Postgres for migrations and database-backed readiness checks

## Local Setup

From `backend`:

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

The service runs against the **shared remote Postgres** (`<postgres-host>:<port>/<database>`) —
`DATABASE_URL` comes from `backend/.env` (untracked; see `.env.example` for the shape). The
default test run needs no database at all (isolated SQLite); Postgres-gated tests opt in via
`TEST_DATABASE_URL` and run in disposable per-run schemas, so the remote database is safe to
test against. The bundled Docker Compose remains available for fully-local/offline work.

## Run The API

```bash
mise run risk-service:dev
```

Database: the remote Postgres is already migrated to head — with `backend/.env` in place no
export is needed. Two operational notes for the remote:

- **RLS hides everything without the tenant GUC.** Ad-hoc `psql` against the remote shows zero
  rows on tenant tables (`FORCE ROW LEVEL SECURITY`); set
  `SELECT set_config('app.organization_id', '<org-uuid>', false);` first when inspecting.
- **The single remote role has no BYPASSRLS**, so the cross-tenant background worker cannot
  claim queued jobs there yet — request a BYPASSRLS-granted role from the DB host and set it as
  `WORKER_DATABASE_URL` before running the worker against the remote.

Fully-local alternative (offline work):

```bash
docker compose up -d
mise run risk-service:bootstrap-db
export DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service
```

`mise run risk-service:bootstrap-db` creates separate local database roles for migrations and app
runtime, runs Alembic migrations, and grants the runtime role data privileges.
The migration role can bypass RLS for migrations and backfills; the app runtime
role is still created with `NOBYPASSRLS`.
For local test and sample-demo workflows only, it seeds two demo tenants so
audit foreign keys and header-based tenant context work:

```bash
X-Org-Id: 11111111-1111-4111-8111-111111111111
X-User-Id: aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
```

Health endpoints:

- `GET /api/health/live`
- `GET /api/health/ready`

Business API endpoints use URL path major versioning under `/api/v1`. See
`docs/architecture.md` for the API versioning policy.

Authenticated users read their current identity and personal preferences with
`GET /api/v1/auth/me` and update their own nullable `display_name`, `job_title`,
BCP-47-like `locale`, IANA `timezone`, and `light` / `dark` / `system` `theme`
with `PATCH /api/v1/auth/me`. The patch rejects extra fields, so email, role,
organization, and security settings cannot be changed through this endpoint.

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

The default test run uses isolated SQLite databases and never touches Postgres —
the suite explicitly neutralizes any `DATABASE_URL` from `.env` (empty env value =
unconfigured), so a configured remote database cannot leak into tests implicitly.
To run the Postgres-gated tests (migrations, RLS), provide `TEST_DATABASE_URL`;
fixtures create a `risk_service_test_<hex>` schema per run and drop it afterward,
so the shared remote database is safe:

```bash
TEST_DATABASE_URL=postgresql+psycopg://<user>:<password>@<postgres-host>:<port>/<database> \
  mise run risk-service:test-postgres
```

(Local alternative: `docker compose up -d risk-postgres` and point
`TEST_DATABASE_URL` at it.)

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

# Primary database (shared remote; real credentials only in the untracked .env).
# DATABASE_URL=postgresql+psycopg://<user>:<password>@<postgres-host>:<port>/<database>

# Object storage. Document upload, presigned URLs and the storage-health probe
# share ONE credential set with the Data Engine — there is no separate RISK_S3_*
# set. The values below are the LOCAL compose stack (backend/docker-compose.yml);
# deployed environments point S3_ENDPOINT at the real object store.
STORAGE_BACKEND=minio
STORAGE_ENV=mvp
S3_ENDPOINT=http://localhost:9000
S3_REGION=us-east-1
S3_BUCKET=risk-local
S3_ACCESS_KEY=minioadmin
S3_SECRET_KEY=minioadmin
S3_FORCE_PATH_STYLE=true
STORAGE_PRESIGN_EXPIRES_SECONDS=900
RISK_MAX_UPLOAD_BYTES=25000000
```

`RISK_MAX_UPLOAD_BYTES` is the only surviving `RISK_*` variable, and it is a size limit
(25 MB), not a credential. The eight `RISK_S3_*` / `RISK_STORAGE_BACKEND` names this
section used to list set **nothing** — the `settings.risk_*` symbols still in the code
(`risk_storage_backend`, `risk_s3_bucket`, …) are read-only properties over the
variables above, never environment variables.

Storage settings are declared in **two** places and only one of them runs: the live
engine is `app/storage/config.py::StorageEngineSettings` (backend `minio | s3 | gcs`,
default `minio`, and every `S3_*` alias above), used by `storage/factory.py`,
`storage/s3_compatible.py` and `storage/provisioning.py`. The parallel
`app/core/config.py::StorageSettings` redeclares the same aliases with backend pinned to
`Literal["s3"]` and no `STORAGE_BACKEND` alias. Change the former when changing storage
behaviour; the duplication is a known wart.

`psycopg[binary]` is used for MVP setup convenience. Revisit production packaging before hardening deployment images.
