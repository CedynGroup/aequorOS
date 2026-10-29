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

Deployed API and operator listeners start through `python -m app.core.serve api`
and `python -m app.core.serve operator`. Certificate, trust-store, database URL,
and local-development exceptions are governed by
[the transport contract](docs/transport_security.md).

Bank-held object encryption and optional rollout are in
[the bank key setup guide](docs/bank_key_setup.md).
Covered identity fields and identity indexes are in
[the field encryption contract](docs/bank_field_encryption.md).

Bank audit reads, Inspector support-reference capture, audit-chain verification,
paging, filing and session retention, backup
publication and required migration-role preparation are in
[the tamper-evident records contract](docs/tamper_evident_records.md).

- [Deployment health](#deployment-health): process liveness and dependency readiness
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
  Decimal engine under `app/domain/` behind an immutable `RegulatoryRun`. See the
  [ECL assumption and coverage contract](../ARCHITECTURE.md#ecl-assumption-and-coverage-contract)
  for register validation, run refusals and provision handling.
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

Authentication uses the API `DATABASE_URL` sessionmaker. Operator sessions use
`OPERATOR_DATABASE_URL`, falling back only to `DATABASE_URL`. Neither uses
`WORKER_DATABASE_URL`. Keep the existing API credential until the separate
captain-reviewed lookup-function PR is deployed. The worker credential switch
requires this compatibility PR and remains a separate deployment step under the
[database-role handoff](../docs/ops/database-roles.md#worker-compatibility-migration),
which owns activation prerequisites and rollback.
Two operational notes for a remote database:

- **RLS hides everything without the tenant GUC.** Ad-hoc `psql` against the remote shows zero
  rows on tenant tables (`FORCE ROW LEVEL SECURITY`); set
  `SELECT set_config('app.organization_id', '<OR-XXXXXXXX>', false);` first when inspecting.
- **Worker credentials and rollout** follow
  [the deployment runbook](../docs/ops/database-roles.md#worker-compatibility-migration),
  including authentication/operator credential separation and the prerequisites
  for each role's activation.

Fully-local alternative (offline work):

```bash
docker compose up -d
export DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:15432/risk_service
```

The bundled compose uses local superuser credentials and does not demonstrate
production credential separation. Local role preparation and the staged deployment
switches follow the [database role handoff](../docs/ops/database-roles.md#migration-and-local-development-order).
Runtime connection budgets and migration exemptions follow the
[session timeout runbook](../docs/ops/database-roles.md#session-timeout-budgets).
App-session API requests use an access token issued by password or SSO login:

```http
Authorization: Bearer <app-access-token>
```

### Data Engine file ingestion

With an authorized app session, first upload the source file as multipart field
`file` to `POST /api/v1/banks/{bank_id}/ingestion-uploads`. Pass the returned
`location` unchanged to `POST /api/v1/banks/{bank_id}/ingestion-batches`, together
with the source system, reporting date and reason required by the API schema.
The location is an opaque `temp://` handle resolved only within that bank's temp
storage tier. Server filesystem paths and other URL schemes return HTTP 422;
malformed handles are rejected before a batch is created. Upload to the same bank
that will ingest the file, even when two banks share an organization.

### API transactions and external waits

Governed BI exports commit authorization before running the query. Certified
measure metadata stays on the API role; every compiler discovery statement and
the final query commits that metadata read before checking out the BI pool.
Delivery provenance, audit and query logs are then produced in a fresh API
transaction, with one commit before returning the artifact or queued job.
The single-database fallback also ends its query transaction before delivery.
The [database role handoff](../docs/ops/database-roles.md#target-matrix) owns the
API's guarded query-log partition capability.
See [native transaction/role regressions](tests/db/test_bi_export_transactions.py).

Document deletion and upload-completion boundaries are documented under
[the legacy case vertical](#legacy-case-vertical). Remaining transaction-held waits include
`services/ingestion.py:upload_source` (institution setup and object write),
`start_ingestion` (source download and raw-artifact write), and
`services/database_connections.py:test_connection`, `discover_schema` and
`sync_now` (remote database inspection or extraction). These require their own
transaction-boundary work before lowering the API idle budget; the
[session timeout contract](../docs/ops/database-roles.md#session-timeout-budgets)
owns that budget and its review criteria.

### Deployment health

- `GET /api/health/live`: process liveness, HTTP 200 with
  `{"service":"risk-service","environment":"production","status":"ok","bank_key_required":false}`
  (service name, environment and bank-key policy reflect deployment settings).
  This never queries dependencies; use it for process restart decisions.
- `GET /api/health/ready`: existing dependency readiness contract. HTTP 200
  contains `service`, `environment`, `status` and `checks` for database, storage,
  worker, AI and signing. `status: "degraded"` remains HTTP 200 for overdue core
  or AI jobs; monitors wanting that signal must inspect the JSON.
  Required dependency/configuration failures return HTTP 503 in the
  standard error envelope, for example
  `{"error":{"code":"service_unavailable","message":"Storage connectivity check failed.","request_id":"…"}}`.
  Each caller has a five-second response deadline. Overlapping callers share
  the active dependency check and receive its result if it finishes within their
  own deadline. A timeout returns 503 with `Readiness check timed out.` without
  cancelling the shared check; later callers can still await it. At most one
  dependency check runs per API process, and the next request after completion
  starts a fresh check. Liveness stays independent while readiness is waiting.
  This assumes one API event loop per process: selecting and replacing the
  shared future is synchronous, without an intervening await.

Both URLs are unauthenticated, return `Cache-Control: no-store` on health
responses, and require HTTPS on deployed services. The existing container
monitor already calls `https://risk-api:8000/api/health/ready` with the service
CA and an eight-second client timeout. Keep using readiness to gate deployment,
and liveness for process checks; do not restart a healthy process merely because
an external dependency is down. External monitors can request the same paths on
the configured API origin without cookies, bearer tokens or following redirects:

```bash
curl --fail-with-body --max-time 8 --include "$API_ORIGIN/api/health/live"
curl --fail-with-body --max-time 8 --include "$API_ORIGIN/api/health/ready"
```

The dashboard server has its own independent
[process liveness endpoint](dashboard/README.md#deployment-health).

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

Document deletion commits the document tombstone, the stored object's
`pending_deletion` status, the audit event and a `document_delete` job atomically.
The existing deleted response means logical deletion: downloads, parsing and
upload completion refuse the document immediately. Upload completion commits its
read transaction before storage HEAD, then reloads locked rows and revalidates
the pending upload. Completion returns HTTP 409 if deletion committed during HEAD,
even if cleanup has already finished. Downloads also refuse tombstones with
inconsistent live statuses. The core worker closes its read transaction before
deleting from storage, then commits the object's
`deleted` acknowledgement with job completion. A transport failure or lost
acknowledgement leaves pending cleanup safe to retry; duplicate requests preserve
one job and one deletion event. Existing deleted records remain unchanged.
The queue's bounded exponential retries and stale-worker reclaim apply; exhausted
cleanup remains pending and appears as a failed job on the operator jobs board.
See [`documents.py`](app/services/documents.py) and the
[deletion regression tests](tests/services/test_document_deletion.py), including
[PostgreSQL concurrency and role-timeout coverage](tests/db/test_document_deletion_timeout.py).

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

The [workflow inventory and stateful-test guide](docs/state_machine_testing.md)
records transition guards, coverage boundaries, and bounded Hypothesis profiles.

```bash
mise run unit         # database-free tests; any database connection fails
mise run integration  # database/external-service tests on disposable Postgres
mise run risk-service:test-e2e  # backend Playwright contracts
```

These commands work at the repository root or in `backend/`. Their explicit
pytest selectors are `-m unit`, `-m integration` and `-m e2e`; unit and integration
also have `risk-service:test-unit` and `risk-service:test-integration` aliases.
Install dashboard dependencies with `pnpm install --frozen-lockfile` at the repository
root before running the e2e suite or the full census.

Feature-local paths stay in place, preserving imports and fixture/data paths.
[`scripts/test_suites.json`](scripts/test_suites.json) is generated from observed
database and external-service requirements for every exact parametrized test ID.
There are no module defaults or manual function overrides. Collection rejects new/unclassified IDs,
duplicate collection and conflicting suite markers before marker deselection.
SQLite counts as a database. Optional primary-data tests retain their explicit
opt-in; the census never supplies primary-bank data.

Refresh ownership after adding tests or changing database or service requirements,
including tests brought in by merging main. The merge queue executes the combined
tree, so a catalogue checked only against an older PR head is insufficient:

```bash
mise run risk-service:refresh-test-suites /absolute/path/to/empty/census-evidence
```

This runs the entire test tree serially with disposable native PostgreSQL 17 and
MinIO plus its own loopback OpenBao, clearing ambient database and primary-data URLs.
Install OpenBao or set `AQS_OPENBAO_BIN` to its verified binary. See the local-services
guide below for installing the native binaries. To use disposable Docker instead,
run `uv run python -m scripts.test_suite_census refresh <evidence-dir> --mode docker`
from `backend/`. Keep the evidence directory outside the repository. The full run
can take substantially longer than the unit suite; retain its log and let fixture
teardown and child processes finish before generation. Commit the regenerated
catalogue. For a service-gated case requiring another applicable profile, work from
`backend/` and collect that profile with `CI_DATABASE_CENSUS=1` and `CI_DATABASE_OBSERVATIONS=<separate-dir>`,
then run `uv run python -m scripts.test_suite_census generate <full-dir> --supplement <profile-dir>`.
The supplement must contain only identities from the full census, and conflicting
proven ownership across profiles is rejected. A failing run returns nonzero and reports its inconclusive cases;
fix failures before claiming full validation.
For newly merged tests, `--extend <complete-new-test-profile>` adds disjoint
identities with the same execution evidence requirements; overlapping identities
are rejected. Existing ownership still comes from the retained full census.

The observer records driver connection attempts and SQLAlchemy pooled checkouts
across setup, call, teardown, shared fixtures and inherited Python subprocesses.
Shared-fixture evidence applies to every consumer, including failed setup and
cached setup failures; nested fixtures and registered finalizers retain attribution.
An attempted acquisition counts as database use even when it fails or is caught:
the unit guard must not replace a driver's expected refusal. A fully passing case
with no observed database or external-service use and no unconfirmed children is
unit-owned. Positive database acquisition evidence rules out unit ownership even
when execution fails or a child does not confirm instrumentation; neither profile
may replace it with proven unit ownership. Without a database or service requirement,
skipped/failed cases and unconfirmed children retain conservative integration
ownership and appear in `inconclusive.json`. A skipped case is not
proof that an integration test could be moved; run its applicable profile to obtain
evidence. This preserves explicit ownership without changing product assertions.

The unit owner has no PostgreSQL, MinIO, OpenBao or dashboard dependency setup.
Observed MinIO/OpenBao connections and native service processes are integration
requirements; Playwright launches are e2e requirements. A declared
`requires_service` marker covers contracts whose assertions consume state from
earlier service-using cases and service-gated contracts such as the local KMS.
The generated catalogue also records these service requirements so the aggregate
rejects skipped service contracts. To enrich an
older database-only census, pass `--services <complete-service-profile>` to
`generate`; only positive service requirements can change ownership. MinIO runs
in storage/schema jobs and the shard containing native service contracts;
OpenBao runs in its integration shard. Backend Playwright contracts have one
explicit e2e job beside dashboard journeys. Every test has one of three owners:
unit, integration or e2e.

Every CI owner emits the same observations alongside its JUnit report. The final
integration gate invokes `scripts/test_suite_census.py check` on those existing
executions: observed unit database/service use and fully executed integration/e2e cases without
their observed dependency both fail with the exact IDs to refresh. The independent partition
guard proves the observed selection has exactly one execution owner, including
conservative affected-module CI runs. Original database-free files remain in
place; classification never changes test imports or fixtures.

The unit guard supplies a loopback port-1 `TEST_DATABASE_URL` sentinel before
collection. This enables model-only checks inside mixed migration modules whose
module-wide skip condition requires that setting. No PostgreSQL service is
provisioned, and any attempted use of the sentinel fails the connection guard.

A dedicated unit run selects only generated unit-bearing modules before imports,
then prunes known non-unit functions before parametrization. Unknown test functions
and parameter identities fail collection. With CI evidence enabled, missing cases
also fail collection and the filesystem inventory must match the catalogue.
Aggregate execution reports prove the other owners'
exact coverage; a second full-tree collection is unnecessary. Integration-only
modules are never imported by this selector. Mixed modules still share their
module imports, while their known integration/e2e functions are not collected.
Numeric libraries use one thread per worker to avoid nested OpenMP/BLAS pools
when several ML cases fit concurrently. The run uses the runner's detected CPU
count (`-n auto`) and per-case load scheduling, retaining
the in-memory storage audit sequence on one worker because it shares mutable
class state. The immutable OpenAPI schema and generated shard reports also stay
on one worker each, avoiding repeated expensive module-fixture setup. `--durations=20` reports setup/call/teardown outliers. Database
connections are refused before collection. Each process records attempts separately;
the controller requires identical worker classifications and exact combined JUnit
coverage. Combined local runs also refuse connections for each unit test, before
fixture setup. Finalization follows fixture ownership: integration-only fixture
cleanup remains permitted when the last test is unit-owned, while fixtures consumed
by a unit test retain refusal during their finalizers.
SQLAlchemy, psycopg (including async), SQLite and installed database-direct driver
entry points are guarded, including Python subprocesses with fresh environments.
Ordinary Python children start with stdlib-only instrumentation; a loader wraps
database drivers before their imports return instead of eagerly importing every
SDK into tiny subprocess probes. SQLite also has a process-wide audit guard.
Grandchildren retain refusal and observation even with a fresh environment.
Attempts fail the run even when caught. The unit CI owner compares the complete
classified collection with JUnit identities and permits no dropped, duplicated,
skipped, failed or errored unit case. It uploads `unit-tests` classification,
connection-attempt and execution evidence. Module inventory and exact case coverage remain enforced in that same collection.

`mise run risk-service:test` remains the combined compatibility run:

```bash
mise run risk-service:test
```

The default test run uses isolated SQLite databases and never touches Postgres —
the suite explicitly neutralizes any `DATABASE_URL` from `.env` (empty env value =
unconfigured), so a configured remote database cannot leak into tests implicitly.
For a local feedback loop, run `mise run risk-service:test-fast`. It compares the
current branch and staged/unstaged edits with the merge base of `origin/main`.
It reuses CI's conservative affected-module rules described below. When those
rules cannot narrow the selection, it runs the complete hermetic suite instead.
Untracked files, a missing base and an empty diff also force this fallback, with
an explicit message.
Both modes use four workers and `--dist loadfile` to keep each module's fixtures
together.
This profile is local and opt-in; the default test tasks and full main CI stay unchanged.

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
available; point `TEST_DATABASE_URL` at it to reuse that service. Supplied services
must also meet the [role-test preparation requirements](../docs/ops/database-roles.md#migration-and-local-development-order).

CI starts native PostgreSQL 17 through
[`setup-postgres`](../.github/actions/setup-postgres/action.yml), shared by the
schema/RLS/locks, storage and full-suite jobs. It verifies the server version and
raises `max_locks_per_transaction` to 256 before creating the restricted test roles.
The migration chain requires no extensions: `gen_random_uuid()` is built into
PostgreSQL 17. If a migration adds an extension dependency, install its PostgreSQL
17 package in that action and verify availability before running migrations.

Run the storage contract suite with `mise run risk-service:test-storage`. Its
wrapper supplies disposable PostgreSQL for storage integration cases and MinIO
for the object-store contract unless `TEST_DATABASE_URL` and `S3_*` settings
already supply those services. The dedicated CI storage job supplies both and
rejects any skipped test.

The schema task also supplies MinIO for the document cleanup regression. Each
CI schema shard starts it and creates the document test bucket before pytest;
the schema aggregate continues to reject every skip.

CI's [MinIO action](../.github/actions/setup-minio/action.yml) and
[OpenBao action](../.github/actions/setup-openbao/action.yml) download official
release binaries and verify pinned SHA256 digests before execution. MinIO uses a
disposable built-in KMS key named `aequoros-key`; it also serves
[dashboard journeys](dashboard/README.md#end-to-end-playwright).
The integration shard owning live signing cases also starts OpenBao. Both
services listen on loopback
and stop at job cleanup. Other API,
health and operator provisioning tests retain their in-process storage fakes;
credential-vault encryption tests need no vault server. The live storage and
OpenBao execution guards remain in
[Risk Service CI](../.github/workflows/risk-service.yml).

The integration aggregate also joins the lock and storage execution reports with
unit, e2e and Postgres shard evidence. Their selected union must match the classified
collection exactly once. Storage integration cases run only in the storage job,
except key-rotation concurrency, which belongs to locks. The storage job also
runs for merge groups. A conservative affected-module run excludes only the
explicitly unselected suite cases from this cross-owner comparison.

The **Unit tests** job runs `risk-service:test-unit`, including database-free
architecture guards. Database-using architecture cases run in the integration
shards.

CI runs the remaining PostgreSQL suite in four duration-balanced module shards,
and the schema/RLS suite in two hash-based shards; lock tests retain their own job.
Longest-first packing uses the committed `scripts/ci_postgres_durations.json`
baseline, excluding files with a non-Postgres owner. Each file stays together, including OpenBao's shared AppRole fixtures.
New modules use stable hashing until the baseline is refreshed. The
**Integration tests** aggregate joins the suite shards; **Postgres schema, RLS and locks**
and the always-reported **Risk service gate** remain enforced. Shards upload their complete
collection manifests and JUnit reports; the aggregates verify that their union
contains every expected test exactly once, preserve the full-suite execution
floor, and reject any skipped schema/RLS or live OpenBao test.
Failed schema and suite shards also upload `postgres.log` as
`pg-schema-server-log-<shard>` or `pg-suite-server-log-<shard>` when the log
exists, retained for seven days.

Main pushes and queued merge results always run the full suite. On pull requests,
only changes confined to the
bodies of existing, unreferenced `tests/domain/test_*.py` tests owned by the
Postgres suite (including nested domain directories), or comments and formatting
in those modules, select their affected modules. Changes to fixtures, decorators, production code, new/deleted
tests, configuration or an uncertain diff select the full suite. Schema/RLS,
locks, unit and storage gates keep their coverage. The stdlib planner/coverage verifier is
[`scripts/ci_postgres.py`](scripts/ci_postgres.py); pytest selection is opt-in via
[`scripts/ci_postgres_plugin.py`](scripts/ci_postgres_plugin.py). Default local test
tasks continue to run their complete suites; `risk-service:test-fast` uses the
local profile described above.

Architecture guards still scan every module and run every check. Their shared
scanner memoizes immutable facts by source text and import/table context within
the current process; each caller receives a fresh set. Read-only route guards
inspect the served application's route graph instead of rebuilding the same app
for each assertion. Changed source is scanned again automatically.

Refresh the duration baseline after a successful **full** main run, using its four
`pg-suite-*` artifacts (download them into separate directories under one parent):
run these commands from the repository root at that run's commit, before changing
the baseline. Verification recomputes shard assignments from the baseline in that
revision, so reports from another revision may be rejected.

```bash
gh-axi run download <run-id> --name pg-suite-1 --dir /tmp/pg-reports/pg-suite-1
# Repeat for pg-suite-2, pg-suite-3 and pg-suite-4.
python3 backend/scripts/ci_postgres.py refresh-durations /tmp/pg-reports \
  --source-run https://github.com/CedynGroup/aequorOS/actions/runs/<run-id>
```

The refresh verifies exact collection/execution coverage, the 3,000-executed
floor and live OpenBao execution before replacing the baseline. JUnit case times
include setup, call and teardown. Review and commit the resulting baseline; CI
never writes it automatically. Refresh after substantial fixture or test layout
changes, and compare actual job timings rather than treating summed test durations
as wall time. The baseline's `source_run` links the complete successful hosted run used for its
weights. On an ownership change, verify the source artifacts against the old
assignment before removing transferred modules from the refreshed weights.

### Test databases and the primary database

- **Live-data invariant suite** (`backend/tests/live_data/`): read-only checks against the
  ACTUAL primary database — provenance (every canonical row ingestion-traced; the
  executable form of the no-seeding order), period-spine contiguity, fact coverage,
  live-metrics presence, sign-in capability. Opt-in:
  `LIVE_DATA_DATABASE_URL=<authorized read URL> uv run pytest tests/live_data`.
  With a tenant-scoped credential, set `LIVE_DATA_ORG_ID` to the `OR-…` platform ID;
  an existing authorized BYPASSRLS credential can inspect all tenants. The new
  `aqs_worker` credential provides no unscoped tenant visibility. The session is server-side read-only —
  it cannot mutate what it certifies. Hermetic suite stays the home of mutation/logic
  tests; never point mutating tests at the primary DB.
- Hermetic and Postgres-gated mutation checks follow [Run Tests](#run-tests), including
  its dedicated test-database requirement. Tests against the primary are limited to the
  read-only live-data suite above.
- Test connection construction, fixture selection and the direct-constructor ratchet
  are documented in the [test connection guide](tests/support/DATABASE.md).
- **`Base.metadata.create_all` skips migrations.** The hermetic pytest suite and the
  shared Playwright bootstrap therefore use `tests/fixtures/reference_data.py` to seed
  every GLOBAL registry from the same catalogues
  the migrations read (`jurisdictions`; `institution_types.seed_rows`;
  `regulatory_parameters.seed_rows`) — add a new registry there once and both callers get
  it. A missing registry surfaces as a fail-closed 409 naming a seed migration.
  Shared live-plane preparation, the separate real-worker journeys and full Playwright
  prerequisites (object storage included) are owned by the
  [dashboard end-to-end guide](dashboard/README.md#end-to-end-playwright); for SSO, see its
  [local issuer guidance](dashboard/README.md#single-sign-on-against-a-local-issuer).
  **Default shared test databases are built once per pytest process**
  (`backend/tests/conftest.py`): tenant API rollback-isolated tests share one
  schema through a savepoint-bound sessionmaker, and `@pytest.mark.committing_db`
  tests share a second schema that is TRUNCATEd and reseeded before each test.
  Both default tenant fixture families reuse one application per process.
  BI export header cases (`tests/bi/api/test_bi_exports.py`) reuse a separate
  module-scoped app, built lazily after hermetic settings, BI flags and the fixed
  CORS origin are installed. Each case installs its current dependency overrides,
  opens a fresh client and clears overrides in `finally`; sessions and storage
  fakes remain function-scoped. Settings-sensitive CORS/TLS tests must build their
  own app.
  Bank audit API tests reuse a module-scoped migrated schema and lazily constructed application
  (`tests/identity/api/test_audit_log.py`), with fresh clients, per-test rollback and
  `aqs_api` authority for HTTP database dependencies; the dependency override is
  restored at teardown. Operator tests reuse a separate application and SQLite database,
  with the same rollback isolation
  (`tests/operator/conftest.py`). Each client is function-scoped, with storage
  dependency overrides removed at teardown. Migration tests and feature-local fixtures
  requiring migrated DDL build isolated schemas of their own. The `catalog_postgres_schema`
  fixture shares a module-scoped forward-migrated schema only for read-only catalog and
  migration-seeded reference-data assertions;
  mutating migration tests retain isolated schemas, and `migrated_postgres_schema`
  still verifies the upgrade/downgrade round trip. A test that needs a fresh schema
  is the exception to justify, not the default to reach for.
  `risk-service:test-postgres-suite` runs under pytest-xdist (`-n 4 --dist loadfile`),
  using at most four workers. Each worker lazily builds its own rollback and
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

Alembic connection selection and role requirements are governed by the
[migration credential contract](docs/tamper_evident_records.md#required-deployment-follow-up).

```bash
mise run risk-service:migrate
```

To create a migration revision:

```bash
mise run risk-service:revision "describe change"
```

## Environment Variables

For bank-key rollout settings, AWS policies, onboarding, rotation and recovery,
follow [bank key setup](docs/bank_key_setup.md). Custody and enforcement are
governed by [the bank encryption contract](docs/bank_encryption.md).

Copy [`.env.example`](.env.example) to `.env` for the current local settings,
including the explicit plaintext opt-in. Document uploads, presigned URLs,
storage health, and Data Engine canonical tiers share that one storage
credential set. For deployed settings, follow
[the transport deployment contract](docs/transport_security.md#deployment-preparation-review-before-applying).

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
