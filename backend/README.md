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
- Scoped authorization bindings with deny-by-default, explainable evaluation
  and explicit organization-or-exact-institution targets. See the
  [product rollout contracts](docs/authorization_foundation.md#product-rollout-boundary)
  for enforced product surfaces and required grants.
  Account administration is split out into the non-operational `account_admin`
  role, while initial Org Owner authority is an explicit organization-wide
  binding. Migration `202608280046` assigns it only for exactly one eligible
  active human legacy admin; zero/multiple candidates require later staff
  designation. App access and refresh tokens carry `authv`, so authorization
  migrations and later authorization changes can invalidate every session
  immediately. Org Owners can preview, create, list, and revoke one complete
  scoped grant at a time; Access → Members aggregates tenant identities and
  binding evidence, and SSO request approval atomically activates the identity
  with one grant. See `docs/authorization_foundation.md`.
- Data Engine: Excel/CSV upload, push API, market-data adapters, and the
  database-direct adapter (see the deployment note below)
- Six calculation modules — liquidity (LCR/NSFR/stress/LMT), Basel capital
  (RWA/CAR/stress), IRRBB, FX, FTP, and balance-sheet forecasting — each a pure
  Decimal engine under `app/domain/` behind an immutable `RegulatoryRun`
- Live engine: authoritative ingestion, market-data, governed-input, entitlement,
  and reconciliation mutations enqueue debounced `pipeline_refresh` jobs that
  re-derive facts and update `live_metrics`/`live_findings`; `GET live-summary`
  is read-only. Structural unavailability remains stable until an input changes,
  while true module exceptions retry the same job with bounded backoff and persist
  their classification, attempt count, and next due time. `official_run` jobs mint
  the immutable filing runs
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
`TEST_DATABASE_URL`; use a dedicated test database as described under
[Run Tests](#run-tests). The bundled Docker Compose remains available for fully-local/offline work.

## Run The API

```bash
mise run risk-service:dev
```

Database: configure `backend/.env` and apply the migration chain before starting the API.
Two operational notes for a remote database:

- **RLS hides everything without the tenant GUC.** Ad-hoc `psql` against the remote shows zero
  rows on tenant tables (`FORCE ROW LEVEL SECURITY`); set
  `SELECT set_config('app.organization_id', '<OR-XXXXXXXX>', false);` first when inspecting.
- **The cross-tenant worker needs a BYPASSRLS role** to claim queued jobs from FORCE-RLS
  tables. Configure that separate role through `WORKER_DATABASE_URL`; keep the application
  role `NOBYPASSRLS`.

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
For local test and sample-demo workflows only, it seeds two demo identities for
audit foreign keys. The script still prints their legacy tenant-header values
for old fixture tooling, but the API does not trust those headers. Business API
requests require an app access token issued by password or SSO login:

```http
Authorization: Bearer <app-access-token>
```

Health endpoints:

- `GET /api/health/live`
- `GET /api/health/ready`

Business API endpoints use URL path major versioning under `/api/v1`. See
`docs/architecture.md` for the API versioning policy.

Authenticated users read their current identity, personal preferences, and
server-evaluated effective-authority projection with `GET /api/v1/auth/me` (or read
the projection alone with `GET /api/v1/auth/effective-authority`) and update their
own nullable `display_name`, `job_title`,
BCP-47-like `locale`, IANA `timezone`, and `light` / `dark` / `system` `theme`
with `PATCH /api/v1/auth/me`. The patch rejects extra fields, so email, role,
organization, and security settings cannot be changed through this endpoint.

### Forecast assumption workflow

A newly provisioned bank has no forecast assumption values. Its provisioning
result names the missing approved base, adverse and severely adverse assumptions;
forecasting remains not computable (`missing_parameter`) until an approved set is
effective for the reporting period's book date. One-off run overrides do not
replace that prerequisite.

Use the tenant API under `/api/v1/banks/{bank_id}/forecast/assumption-versions`:

1. Read the register with `GET` to see the effective version for the latest book
   and any open proposal. `GET /{version_id}` reads one version.
2. Propose the bank's complete set with `POST`, using the request contract in the
   API's generated `/docs` reference (`ForecastAssumptionVersionCreate`). Revise
   a draft with `PATCH /{version_id}`.
3. Submit it with `POST /{version_id}/submit`. An independent authorized user
   decides it with `POST /{version_id}/approve` or `POST /{version_id}/reject`;
   approval accepts `{}`, while rejection requires a reason in `note`.

The [Forecasting authorization contract](docs/forecasting_enforcement_rollout.md#affected-surfaces)
owns the required scoped bindings and checker independence. Account administration
alone grants no Forecasting authority. The
[governed-assumption architecture](../ARCHITECTURE.md#governed-forecast-assumptions)
owns effective-date selection, final-version rules and saved-run immutability.

Scenario catalogue and forecast, what-if and optimizer reads expose the resolved
version as `assumption_version`; generic regulatory-run reads expose its saved
record as `assumption_provenance`. Consult the generated API reference for those
provenance fields. The dashboard's current capabilities are documented in
[Forecasting tools](dashboard/README.md#forecasting-tools).

### Legacy case vertical

Canonical financial data is read with
`GET /api/v1/cases/{case_id}/financial-workspace`. Resource-specific `POST` and
`PATCH` routes below that path support institutions, accounts, reporting
periods, balances, cash flows, obligations, and covenants. These mutations
require an authenticated mutation-capable bearer principal; each request body
requires a non-empty `reason`. Successful responses contain the updated `record` and the case's
refreshed `validation` state. The request and response schemas live in
[`app/schemas/financial_workspace.py`](app/schemas/financial_workspace.py);
[`app/services/financial_canonical_edits.py`](app/services/financial_canonical_edits.py) owns
correction-history behavior.

Clients use the generated `FinancialDataApi` from `packages/risk-service-api`
for manual entry and correction, with account and obligation statuses constrained
to the generated contract values. To have the backend derive covenant compliance
from the inputs, omit `complianceStatus` from the request. Dashboard client
conventions are owned by [CODEBASE_CONVENTIONS.md](../CODEBASE_CONVENTIONS.md#api-access).

Case scenarios are read from `GET /api/v1/cases/{case_id}/scenarios`. Initialize
the baseline and downside defaults with `POST .../scenarios/initialize`, or use
the resource-specific scenario, copy, archive, assumption, and review routes
below that path. All mutations require an authenticated mutation-capable bearer
principal and a non-empty `reason`. An active scenario is calculation-ready only when it has a non-null,
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
provided fields override those values. Both mutations require an authenticated
mutation-capable bearer principal.

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

Creating a projection requires `calculation_run_id` and an authenticated
mutation-capable bearer principal. The run must be successful and belong to an active scenario in the
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
reason. Review requires an authenticated mutation-capable bearer principal, records audit events, and is
rejected for terminal findings or findings belonging to archived scenarios.
The generic findings update endpoint does not mutate liquidity workflow
findings. A newer successful run supersedes open findings from the previous run
for that scenario without altering acknowledged or dismissed history.

### Stale local processes

- **Long-lived local processes serve STALE CODE and the port check will not save you.**
  Several backend processes can run from one checkout against the primary database on
  different ports without any port conflict (uvicorn `--reload` shares one socket between
  supervisor and child). And a port conflict would not help: the damage is done by the
  **in-process live-engine worker thread**, which needs no port, polls the shared `jobs`
  table and writes `live_metrics` with whatever code its process holds. A new feature can
  therefore be verified green in a fresh process while the app serves the old behaviour
  from an old one.
- **The standalone worker is the one that bites, and it has NO `--reload`.**
  `python -m app.worker` is a separate process from `fastapi dev`; it never reloads on a
  code change, and it is what writes `live_metrics`. A cleanup that greps only
  `fastapi dev|uvicorn|app.main` MISSES it. Use the full pattern and check `lstart`
  against your edits:
  ```
  ps -eo pid,lstart,command | grep -E "fastapi dev|uvicorn|app\.main|app\.worker|app\.operator" | grep -v grep
  ```
  Restart the worker after ANY change to a service it dispatches (`fact_derivation`,
  `implied_rating`, the module engines) or its output is a lie about your code.

## Run Tests

```bash
mise run risk-service:test
```

The default test run uses isolated SQLite databases and never touches Postgres —
the suite explicitly neutralizes any `DATABASE_URL` from `.env` (empty env value =
unconfigured), so a configured remote database cannot leak into tests implicitly.
To reuse an existing Postgres service for the gated tests (migrations, RLS),
provide `TEST_DATABASE_URL` for a dedicated test database, never the primary database.
Fixtures create disposable `risk_service_test_<hex>` schemas and drop them afterward.
Schema isolation does not authorize running mutating tests against the primary:

```bash
TEST_DATABASE_URL=postgresql+psycopg://<test-user>:<password>@<test-host>:<port>/<test-database> \
  mise run risk-service:test-postgres
```

Without a supplied URL, the task provisions worktree-local Postgres, using
native PostgreSQL 17 when Docker is unavailable. Set `AQS_LOCAL_SERVICES=native`
to force that mode. Native MinIO setup, isolation and shutdown are documented
in the dashboard's [local service guide](dashboard/README.md#local-services-without-docker-or-orbstack).
The existing `docker compose up -d risk-postgres` alternative also remains
available; point `TEST_DATABASE_URL` at it to reuse that service.

### Test databases and the primary database

- **Live-data invariant suite** (`backend/tests/live_data/`): read-only checks against the
  ACTUAL primary database — provenance (every canonical row ingestion-traced; the
  executable form of the no-seeding order), period-spine contiguity, fact coverage,
  live-metrics presence, sign-in capability. Opt-in:
  `LIVE_DATA_DATABASE_URL=<worker URL> uv run pytest tests/live_data` (BYPASSRLS worker
  URL for visibility, or set `LIVE_DATA_ORG_ID`). The session is server-side read-only —
  it cannot mutate what it certifies. Hermetic suite stays the home of mutation/logic
  tests; never point mutating tests at the primary DB.
- Hermetic and Postgres-gated mutation checks follow [Run Tests](#run-tests), including
  its dedicated test-database requirement. Tests against the primary are limited to the
  read-only live-data suite above.
- **Anything built with `Base.metadata.create_all` runs no migration and no worker, so it
  must seed what those two would have written.** That is the hermetic pytest
  suite AND the Playwright stack (`scripts/e2e_bootstrap.py`). Two shared fixtures own it:
  `tests/fixtures/reference_data.py` seeds every GLOBAL registry from the same catalogues
  the migrations read (`jurisdictions`; `institution_types.seed_rows`;
  `regulatory_parameters.seed_rows`) — add a new registry there once and both callers get
  it — and `tests/fixtures/live_plane.py` stands in for the worker's `pipeline_refresh`,
  because every Treasury/ALM cockpit reads `current_financial_facts`, which only the worker
  writes. Skip either and the failure is late and misleading: a missing registry surfaces as
  a fail-closed 409 naming a seed migration, a missing live plane as "no computed data yet"
  on every module page. Full prerequisites (object storage included):
  `backend/dashboard/README.md` §End-to-end; for SSO, see its
  [local issuer guidance](dashboard/README.md#single-sign-on-against-a-local-issuer).
  **Test databases are built once per pytest process, never per test**
  (`backend/tests/conftest.py`): tenant API rollback-isolated tests share one
  schema through a savepoint-bound sessionmaker, and `@pytest.mark.committing_db`
  tests share a second schema that is TRUNCATEd and reseeded before each test.
  Both tenant fixture families reuse one application per process. Operator tests
  reuse a separate application and SQLite database, with the same rollback isolation
  (`tests/operator/conftest.py`). Each client is function-scoped, with storage
  dependency overrides removed at teardown. Only `tests/db` migration tests build
  schemas of their own. A test that needs a fresh schema is the exception to justify,
  not the default to reach for.
  `risk-service:test-postgres-suite` runs under pytest-xdist (`-n auto --dist loadfile`),
  using one worker per detected CPU. Each worker lazily builds its own rollback and
  committing schemas as needed, and a module's tests stay on one worker. CI's
  `--junitxml` output remains a single combined report for the execution-floor guard.
  Two consequences: test ids must be deterministic (xdist refuses to start when workers
  collect different ids, so never put `uuid4()` or set order into a parametrize id), and
  a test may not depend on another module having run first. The separate `tests/db`
  schema/migration task stays serial to avoid exhausting Postgres' shared DDL lock
  table (`max_locks_per_transaction`).

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
