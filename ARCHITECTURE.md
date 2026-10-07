# AequorOS Architecture

Single source of truth for agents building new modules. When this document and the code
disagree, the code wins — fix this file.

Companion document: [CODEBASE_CONVENTIONS.md](CODEBASE_CONVENTIONS.md).

---

## 1. System map

| Component                      | Path                         | Stack                                                                                                                         | Role                                                                                                         |
| ------------------------------ | ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| Risk service                   | `backend`                    | FastAPI, Python 3.13, uv, SQLAlchemy 2.0, Alembic, Pydantic v2, Loguru, boto3                                                 | The backend. Owns all persistence, calculation engines, findings, audit, and the OpenAPI contract.           |
| Generated API client           | `packages/risk-service-api`  | typescript-fetch output of openapi-generator 7.13                                                                             | Generated from the risk-service OpenAPI schema. Source-consumed (`main: ./src/index.ts`), never hand-edited. |
| Marketing site                 | `frontend`                   | [Marketing stack](frontend/README.md#stack)                                                                                   | Static marketing site. **Out of scope for this build. Do not touch.**                                        |
| Product UI                     | `dashboard`                  | [Dashboard stack](backend/dashboard/README.md#stack)                                                                          | The Treasury Workbench — consumes the risk service exclusively through `packages/risk-service-api`.          |
| Database                       | PostgreSQL                   | Application connection configured by `backend/.env`; [test database policy](backend/README.md#run-tests)                      | Schema kept at Alembic head; worker role requirements are defined in §3b.                                    |
| Local infra (offline fallback) | `backend/docker-compose.yml` | `postgres:17` on host port **15432**, MinIO on **9000** (console 9001), `risk-minio-init` creates private bucket `risk-local` | Started with `docker compose up -d` from `backend`.                                                          |

Tooling: `mise` (root `mise.toml` proxies every `risk-service:*` task into `backend/mise.toml`),
`uv` for Python deps, `pnpm` workspaces (`pnpm-workspace.yaml` includes `packages/*`, `frontend`, `dashboard`). Pre-commit config is at the repo root
(`.pre-commit-config.yaml`): ruff check/format scoped to `^backend/`, Conventional
Commits enforcement, and a pre-push hook that runs `mise run risk-service:api-fresh`.

Local DB bootstrap: `mise run risk-service:bootstrap-db` creates a migration role (may bypass RLS)
and an app runtime role created with `NOBYPASSRLS`, runs migrations, and seeds two demo tenants.
App connection string comes from `backend/.env` (remote:
`postgresql+psycopg://<user>:<password>@<postgres-host>:<port>/<database>`; local fallback:
`postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service`).

### Product segments and the staff control plane

- **Subdomains are product SEGMENTS, not environments.** The authenticated bank
  product is `bank.aequoros.com`; `corp.aequoros.com` is reserved for corporate
  treasury. Marketing stays on the apex, `api.` is the backend, `bao.` is
  OpenBao. Changing the bank product's host means re-registering two OIDC
  redirect URIs with every bank's IT department, so the cost only grows with
  each SSO customer. The segments are genuinely different products over shared
  engines, not one app with a flag: of the six modules, FTP, Basel capital and
  IRRBB do not transfer to a corporate at all, liquidity transfers in name only
  (LCR/NSFR are Basel ratios, corporate liquidity is cash and covenant
  headroom), and the whole regulatory spine — BoG return families, ORASS,
  filing attestation — is bank-only. The reusable value lives in
  `app/domain/*`, which is pure and must stay that way. A corporate entity is a
  SIBLING of `banks` (a `CO-` platform id alongside `BK-`/`OR-`), never a
  nullable-heavy `banks` row — a corporate has no licence, no jurisdiction
  regulator, no return family. Host-change configuration is owned by
  [dashboard deployment guidance](backend/dashboard/README.md#deploy-to-bankaequoroscom).
- **Staff control plane** (specs `docs/internal/developer.md` and
  `docs/internal/staff_UI.md`, both with as-built notes). The operator API is the
  backend's THIRD entrypoint (`app/operator/`, uvicorn `app.operator.main:app`
  :8100, compose service `risk-operator`; NEVER mounted on the tenant API — a
  route-isolation test pins it) with a cross-tenant BYPASSRLS session; the
  console is the separate `console/` Next.js app (console.aequoros.com, all
  traffic via its `/api/op` proxy). Staff auth mirrors the client model:
  email+password against GLOBAL `operator_users` (separate from tenant identity
  by design; `operator_admin` = super admin, seeded for the founder), OIDC SSO
  secondary, dev bearer token non-production-only. Tenant onboarding runs as a
  saga through `provision_institution`; every operator mutation lands in
  append-only `operator_audit_log`. Workforce domain membership is identity
  evidence, not authorization: OIDC authentication requires a matching active
  `operator_users` row and always takes its explicit role from that row;
  unknown or inactive identities get the same generic 401 as any other invalid
  operator credential.

---

## 2. Tenancy model

Verified in `backend/app/api/deps.py`, `app/db/session.py`, and migration
`alembic/versions/202605250002_enable_tenant_rls.py`.

1. **Verified credential → context.** Every authenticated business request carries
   an HTTP bearer credential. Normal app access tokens are HS256 JWTs whose verified `org`, `sub`,
   legacy `roles`, and `authv` claims form a frozen `TenantContext`; missing,
   malformed, expired, pre-authorization-version, or wrongly typed tokens return
   `401` before service code runs. Integration keys and operator impersonation
   tokens are separate bearer credential types with their own validation and
   lifecycle rules; caller-supplied tenant/user headers never establish identity.
2. **Dependency aliases** (use these, never raw `Depends(...)` in feature modules):
   - `DbSession` — tenant-validated SQLAlchemy session (`get_tenant_db_session`). It stores
     `session.info["organization_id"]` and validates that the org exists and, when present, that
     the actor is an **active user in the same org** whose current
     `authorization_version` matches `authv`.
   - `Tenant` — read context. `MutationTenant` — legacy-role mutation context
     (`analyst` or higher, with demo-mode and impersonation write refusal).
   - `Storage` — the `ObjectStorage` protocol (S3/MinIO), from `app/integrations/storage`.
3. **Postgres RLS as the hard safety net.** A `Session` `after_begin` event in `app/db/session.py`
   runs `SELECT set_config('app.organization_id', :org, true)` on every transaction (Postgres
   only; a no-op on SQLite). Migrations `ENABLE`/`FORCE ROW LEVEL SECURITY` on every tenant table
   and create a policy comparing `organization_id` with
   `nullif(current_setting('app.organization_id', true), '')`. Organization IDs
   are `OR-*` platform strings, not UUIDs.
   **Every new tenant-owned table must get the same RLS treatment in its migration.**
4. **Explicit filters are still mandatory.** Service queries always filter by
   `organization_id` (and `case_id` where applicable) even though RLS exists — for readability,
   index usage, and SQLite test compatibility.
5. **Composite FK pattern.** Child tables carry denormalized `organization_id` (and `case_id`)
   columns and declare composite `ForeignKeyConstraint`s to the parent's
   `UniqueConstraint("id", "organization_id", ...)`, so a child row can never reference a parent
   in another tenant. Exact example in
   [CODEBASE_CONVENTIONS.md](CODEBASE_CONVENTIONS.md#composite-fk-tenant-pattern), taken from
   `app/models/calculation.py`.

### 2.1 Authorization transition

Migration `202608250044` adds a FORCE-RLS `authorization_bindings` table and
`users.authorization_version`. Each binding
is one indivisible principal/type + static bundle + organization/institution +
module + sensitivity + provenance + lifecycle tuple. Dimensions inside a row
AND; independently complete rows OR. The pure evaluator starts denied, accepts
only exact active persisted bindings, and requires each resource to name either
the organization or one exact institution; a missing institution never implies
organization-wide scope. It ignores scalar role and token-permission claims and
applies workflow-supplied demo-mode, maker-checker, step-up, and limit
conditions as global vetoes. Its decision includes an audit-ready trace.

Product-route cutovers and their authoritative rollout contracts are listed in
[Authorization foundation — Product rollout boundary](backend/docs/authorization_foundation.md#product-rollout-boundary).
Follow-on migration `202608280046` creates an explicit
organization-wide `org_owner`
binding only where an organization had exactly one active human legacy admin;
zero/multiple-candidate organizations remain unassigned in a queryable
designation state. It also converts every persisted `admin` to the
account-plane-only `account_admin` role and invalidates their sessions. New
staff-provisioned tenants create their first account administrator, owner
binding, and assignment state atomically. Token version enforcement is live:
every app access/refresh token requires positive `authv`;
pre-migration or stale tokens return `401`. Every future role, scope, status, or
security mutation must call
`services.authorization.invalidate_user_authorization()` in its transaction to
advance the version and revoke all refresh families. Full semantics and the
deployment transition are in
[`backend/docs/authorization_foundation.md`](backend/docs/authorization_foundation.md).

Migration `202608290047` adds attributed revocation evidence. Org Owner-gated
tenant routes now preview, create, list, and revoke one complete scalar binding
at a time and aggregate Access → Members. Create and revoke are audited,
assignment SoD is authoritative, and each mutation invalidates the grantee's
sessions transactionally. SSO request approval uses the same complete-grant
path. This administration boundary is enforcing; ordinary product routes are
still on the legacy gates described above.

### 2.2 Institution identity is the platform ID

- **Banks and organizations have no UUIDs — their identity is the platform ID.**
  `organizations.id` (OR-XXXXXXXX) and `banks.id` (BK-XXXXXXXX) are short
  Crockford base32 codes generated by `app/services/public_ids.py` via the model
  defaults — the primary key, API path token, auth `org` claim, RLS GUC value,
  and UI identity. One identity, no aliases: never reintroduce UUID columns or a
  separate "public id" for these two entities (every other entity keeps UUID
  PKs). The hermetic fixture pins `BK-SAMP0001`/`OR-DEM00001` (tests and e2e mint
  against those); real tenants get generator codes at row creation. RLS policies
  compare text — no `::uuid` casts. Records that predate the platform ID keep
  their UUIDs where they were written: migration `202607240025` converted the
  keys and archived the old values in `platform_id_legacy_map`; older
  `audit_events.entity_id` values hold the historical UUID text, and older
  regulatory run input hashes embed the UUID string and stay internally
  consistent with their stored snapshots. New runs hash the platform ID.

---

## 3. The calculation-run pattern (reuse this for every new engine)

The reference implementation is the balance-sheet forecast + capital + liquidity chain. Concrete
tables (all in `app/models/calculation.py` and `app/models/capital.py`, migrations
`202607130002`, `202607130003`, `202607140001`):

| Table                          | Model                       | Purpose                                                                                                                                                                                                                                                                                                                                     |
| ------------------------------ | --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `calculation_runs`             | `CalculationRun`            | One immutable forecast attempt: status, scenario, `rerun_of_run_id`, `engine_version`, `input_schema_version`, `output_schema_version`, `input_hash` (SHA-256 of the canonical JSON snapshot), full `inputs` JSON snapshot, horizon, `as_of_date`, `started_at`/`completed_at`, `error_code`/`error_message`/`error_details`, `created_by`. |
| `calculation_forecast_periods` | `CalculationForecastPeriod` | One row per annual output period, unique on `(run_id, period_number)`, `ondelete="CASCADE"`. Money at `Numeric(20, 4)`.                                                                                                                                                                                                                     |
| `capital_projections`          | `CapitalProjection`         | Immutable capital attempt consuming one **successful** run; copies the run's `input_hash` and currency; own `engine_version` and lifecycle.                                                                                                                                                                                                 |
| `capital_indicators`           | `CapitalIndicator`          | Per-period ratios at `Numeric(12, 8)`, `pressure_level` check constraint.                                                                                                                                                                                                                                                                   |
| `capital_projection_findings`  | `CapitalProjectionFinding`  | Join table linking generated `RiskFinding` rows to the projection.                                                                                                                                                                                                                                                                          |
| `liquidity_analysis_results`   | `LiquidityAnalysisResult`   | Exactly one per successful run (`UniqueConstraint("run_id")`), versioned via `analysis_version`, metrics stored as JSON.                                                                                                                                                                                                                    |

Invariants every new engine must copy (verified in `app/services/calculations.py`,
`app/services/capital.py`, `app/services/liquidity.py`):

- **Immutability + append-only history.** Reruns create a new row (`rerun_of_run_id` link);
  failed attempts are persisted with named diagnostics and never replace prior successful output.
- **Status lifecycle** `queued` → `running` → `succeeded` | `failed`, enforced by a
  `CheckConstraint`. The engine **commits `queued` and `running` before executing**, then opens a
  repeatable-read transaction (`_begin_repeatable_read`) to assemble the input snapshot, so the
  lifecycle contract survives a later move to workers.
- **Snapshot + hash.** The full canonical input snapshot is stored as JSON; its SHA-256
  (`_snapshot_hash`) is stored in `input_hash` and propagated to downstream artifacts (capital
  projections, finding details, evidence locators) for reproducibility.
  - **The hash is value-based, never identity-based.** The snapshot's `facts` list carries only
    economic content (`fact_group`, `category`, `amount`, engine attributes) and is **sorted by
    its canonical JSON**, so the hash is invariant to both fact-row UUID churn and DB return
    order. `fact.id` must **never** enter the snapshot: the live engine re-derives facts on every
    refresh (`fact_derivation` deletes and re-inserts each row with a fresh UUID), so an
    id-dependent hash would make a filed official run non-reproducible after the next data change.
    The `bank-facts-v2` input schema (all six regulatory modules) encodes this rule; `-v1`
    embedded `fact.id` and predates the live engine.
- **Versions as module-level constants**, stored per row:
  `ENGINE_VERSION = "balance-sheet-v1.0.0"`, `INPUT_SCHEMA_VERSION = "calculation-input-v1"`,
  `OUTPUT_SCHEMA_VERSION = "balance-sheet-output-v1"` (calculations);
  `ENGINE_VERSION = "capital-projection-v1.0.0"` (capital);
  `RULE_VERSION = "liquidity-v1.0.0"` (liquidity). Internal version bumps do NOT bump `/api/v1`.
- **Failures are data.** Domain input problems raise a typed exception
  (`CalculationInputError`, `CapitalInputError`) carrying `{code, message, details}`; the service
  persists a `failed` row and still returns `201` with actionable diagnostics. Unexpected
  exceptions persist a sanitized diagnostic.
- **Audit events** (`app/services/audit.py::record_event`) for lifecycle transitions, snapshot
  establishment/rejection, finding generation/supersession/review — recorded in the same
  transaction as the change.
- **Findings + evidence publication** for threshold breaches (section 4).
- **Concurrency**: `SELECT ... FOR UPDATE` on the case/scenario rows before mutating
  (`_lock_active_case_and_scenario`), and Postgres advisory locks to serialize finding
  publication per `(org, case, scenario)` (`liquidity.lock_finding_publication` /
  `serialize_finding_publication`; both no-op on SQLite).

---

## 3a-bis. The three time planes (adopted 2026-08-09)

Every number lives on exactly one of three planes; UI vocabulary and delta
semantics follow the plane, never the other way round:

1. **Position-date plane (data)** — every figure is computed from a dated book.
   Desk headers surface it as "Positions as of {timestamp}" (data provenance,
   not reporting vocabulary). Ingestion cadence sets its resolution.
2. **Live plane (desk)** — the rolling current position: `live_metrics`
   recomputes after authoritative input mutations (debounced), while an optional
   hourly safety net only recovers an input generation newer than its live rows;
   it does not recompute unchanged state. `live_metric_snapshots` cuts one row
   per (bank, day, module) — the day's
   last refresh is the EOD close, today's row is the live edge. Desk deltas
   read this ladder ("vs prior close"); daily sparklines too. Value-based
   hashing guarantees figures cannot drift between refreshes, so "Live"
   without a clock is truthful.
3. **Regulatory plane (governance)** — the **regulator's** reporting dates and
   immutable `regulatory_runs`. Period-over-period trends, filings, and
   freshness-vs-last-official-run live here exclusively.

   **The reporting date is BoG's, never ours (corrected 2026-08-23).** A return's
   reporting dates come from its `ReturnDefinition` — cadence plus the BoG
   anchor conventions — through the one authority
   `services/regulatory_reporting/anchors.py`, which touches no tenant data. The
   dependency runs one way:

   ```
   ReturnDefinition ──▶ reporting date ──▶ snapshot lookup (exact, may miss)
   ```

   `bank_reporting_periods` sits on plane 1, not here: a row is the key for one
   computed fact snapshot, created because a book arrived with an as-of date. It
   is **not** a filing calendar and must never again be offered as the user's
   reporting-date list — doing so made BoG's calendar a function of ingestion
   cadence, which cost the 6 weekly BSD forms 96% of their filing dates (the
   reference tenant had 19 Friday period-ends against 517 Fridays) and gave a
   tenant that had ingested nothing an empty reporting calendar. The snapshot
   match is **exact for every cadence**: a Friday-close return is never
   assembled from a month-end book, and a missing snapshot is refused
   (`no_computed_position`, 409) with the nearest earlier date named for the
   message only.

Growth path: denser ingestion (daily/streaming) makes plane 2 denser and the
live edge fresher without any architectural change — QRM-cadence to
MORS-cadence on the same model.

## 3b. Live engine (two-tier: always-fresh view + immutable official runs)

Ingestion is event-driven, not button-driven. A file upload or API push commits canonical data,
then **enqueues** a background job; the dashboards update on their own. Two computation tiers sit
on one canonical store — the live tier for intraday awareness, the official tier for filing.

- **Job queue** (`app/services/job_queue.py`, reuses the `jobs` table). `enqueue` coalesces on a
  `coalesce_key` (e.g. `refresh:{bank}:{as_of}`) and debounces via `run_after` so a burst of
  uploads collapses into one refresh. `claim_next` uses `SELECT ... FOR UPDATE SKIP LOCKED`;
  `fail_with_retry` backs off through `run_after` up to `max_attempts`.
- **Worker** (`app/worker.py`). A poll loop (process `python -m app.worker`, or in-process behind
  `RUN_INPROCESS_WORKER`) claims → dispatches by `job_type` → completes/retries. It reads the
  `jobs` table **across tenants**, so on an RLS-forced Postgres it must connect with a BYPASSRLS
  role: set `WORKER_DATABASE_URL` (the app role is deliberately tenant-scoped and cannot see the
  queue). Per-job work then runs on a session scoped to that job's `organization_id`. When
  `WORKER_DATABASE_URL` is unset it falls back to `DATABASE_URL` (correct for SQLite tests).
- **Live tier** — `pipeline.run_refresh` (`job_type=pipeline_refresh`): re-derives facts, then for
  each cheap module computes a baseline metric + limit evaluation (`compute_live`, reusing the
  same domain engines as the detail views), **upserts** one `live_metrics` row, and reconciles open `live_findings`
  (continuing breaches keep identity; cleared breaches are superseded). It creates **zero**
  `RegulatoryRun` rows. Forecast computes its current five-year baseline through
  `regulatory_forecasting.compute_live`, independently of saved official runs.
- **Official tier** — `pipeline.run_official` (`job_type=official_run`): reuses
  `data_activation.run_official_modules` to mint the immutable 22-scenario + forecast run set for
  filing. Facts are re-derived only when the period has none, so repeat official runs on unchanged
  facts reproduce the same `input_hash` per run (see the value-based hash invariant in §3).
- **Freshness** (`app/services/freshness.py`, `GET /banks/{id}/freshness`): compares each module's
  live `input_hash` to the latest official run's `input_hash`. Because the hash is value-based, a
  bare re-derivation of unchanged data stays **fresh**; only an actual economic change reads as
  stale ("data changed since last filing run — mint an official run").
- **Alerts** (`app/services/alerts.py`, `GET /banks/{id}/alerts`): open `critical`/`high`
  `live_findings` across modules, surfaced by the header bell (cheap, jittered polling).
- **Scheduler** (`app/services/scheduler.py`): the worker enqueues a `scheduled_tick`; the handler
  enqueues an `official_run` per bank whose daily filing time (`OFFICIAL_RUN_HOUR`) is due. With
  `LIVE_REFRESH_ENABLED`, the same tick is only a recovery net for a bank whose latest ingestion
  is newer than its oldest live module; age alone and structural unavailability are not triggers.
  The official-run schedule is inert unless `OFFICIAL_RUN_ENABLED`, so no environment auto-mints
  heavy runs. Scheduled actor selection and queue attribution follow the
  [FX queued-run contract](backend/docs/fx_enforcement_rollout.md#queued-and-scheduled-official-runs).
- **Refresh authority and retries.** `GET /banks/{id}/live-summary` only reads persisted rows and
  computes its staleness signal; it never enqueues or commits. Accepted ingestion and market-data
  writes, approved methodology/regulatory-parameter changes, tenant assumption/threshold/haircut
  changes, entitlement changes, reconciliation-exception changes, and the explicit refresh action
  enqueue recomputation in their mutation transaction. A module that successfully returns
  `availability=unavailable`, or a reconciliation-blocked book, is stable until such an input
  changes. Only an exception raised by a module retries the same job after 10, 20, then 40 seconds
  (default three-attempt cap); `retry_classification`, `retry_attempt_count`, and `next_retry_at`
  on `live_metrics` expose that state, and successful recovery clears it.
- **Robustness note.** Live compute degrades rather than fails on thin data: FX
  `compute_stressed_var` clamps the cedi-crisis window to the available return history when a bank
  has fewer observations than the configured window, so a short upload still yields a best-effort
  stress instead of killing the whole FX module. On full history the window is used unchanged.

The dashboard polls only cheap live-summary, freshness, alert, and notification signals, with
stable tenant/authority/bank jitter. Signal polls are read-only; a live generation or
official-run change invalidates only the affected module's cached detail. Heavyweight regulatory
dashboards and live-snapshot series do not poll on their own. Their cache keys distinguish current from explicit-period reads and
include tenant, authority, bank, and semantic dimensions; changing tenant or authority remounts
the browser cache. See `backend/dashboard/README.md#query-cache-and-refresh-policy`.

Deferred to a later phase (foundations are laid): true CDC/streaming ingestion (only `full`
snapshot ships), WebSocket/SSE push (signal polling today), per-bank cron UI, email/webhook
delivery.

### Live/governance boundary (enforced)

The platform is Treasury and ALM infrastructure first. The live plane is keyed
by `(organization, bank, module)`, never by a reporting period or
`RegulatoryRun`. Every accepted ingestion debounces into `pipeline_refresh`,
which replaces the bank's `current_financial_facts` materialisation from
accepted canonical state, then upserts each module's live state with source
as-of date, input hash, engine version, generation, computation timestamp, and
pipeline status. Partial failures are retained as explicit failed live state;
they never silently present a previous result as current. Other mutations of
inputs consumed by the live engines enqueue the same coalesced refresh at the
write boundary; reads never repair the calculation plane.

`BankFinancialFact` remains the period-keyed **official/as-of** materialisation
used only by explicit official runs and historical analysis. The nullable
legacy `live_metrics.source_fact_period_id` is no longer written by the live
pipeline; source-as-of date and current-fact generation are the live
provenance. Primary Liquidity, Capital,
IRRBB, FX, FTP, Forecasting, Command Center, Risk & Limits, EWI, and alerts
read current live state. A reporting period or run ID is supplied only for
explicit historical comparison or evidence inspection.

`RegulatoryRun` is governance evidence. `official_run` creates immutable
as-of snapshots; packages seal those snapshots through validation,
maker-checker approval, attestation, export, and submission. Live refreshes
never create or mutate a `RegulatoryRun`, package, or filing artifact.
`GET /banks/{id}/freshness` is consequently governance-only **filing drift**:
it compares current live input hashes with an explicitly selected official
period. Live health is instead `computed_at` plus `pipeline_state`; an aged or
failed refresh is not a filing-drift verdict.

**Anti-pattern:** never use `RegulatoryRun` as the primary source for
day-to-day live ALM/Treasury state. New modules implement a current
`compute_live` path and return a typed live payload; their historical and
official reads must be explicit.

### Live-engine operating rules

- The live engine is two-tier (see §3b above): ingestion enqueues a debounced
  `pipeline_refresh` job that re-derives facts and upserts `live_metrics`/`live_findings` with
  zero `RegulatoryRun` writes, while scheduled/on-demand `official_run` jobs mint the immutable
  filing runs. Endpoints: `GET /banks/{id}/live-summary|freshness|alerts`,
  `POST /banks/{id}/refresh|official-runs`. `GET live-summary` is strictly read-only: ingestion,
  market-data, governed-input, entitlement, and reconciliation mutations are the enqueue
  authorities.
  Module-level `availability=unavailable` is a stable structural result until one of those inputs
  changes; only true module exceptions reuse the same job row's bounded exponential retry, with
  classification/attempt/`next_retry_at` persisted on `live_metrics`.
- **To assess a tenant's health, read what the PLATFORM computed — never call
  `derive_facts` yourself.** The two tiers behave differently by design when a
  book does not reconcile: `derive_current_facts` (live) plugs the gap, stamps
  the fact `status="blocked"` and KEEPS SERVING, because an operator has to see
  a broken book to fix it; `derive_facts` (official) REFUSES, because a date that
  cannot produce a filable book must produce nothing. **A refusal from the
  official path is therefore not a fault signal** — it is the fail-closed design
  working, and a date with e.g. positions but no same-date GL is genuinely not
  filable. Reading it as breakage produces false reports of large
  reconciliation gaps and needless data withdrawals while `live_metrics` says
  `ready`. Health checks read `live_metrics` /
  `GET /banks/{id}/live-summary|freshness|alerts`, or the module's own service
  (`sdi_readiness`, `sdi_views`). `tests/architecture/test_derivation_plane_boundary.py`
  pins the caller allow-list; only `pipeline.run_official`,
  `data_activation.activate_bank_data` and `history_loader` may call the filing
  derivation.
- **The stale-job reclaim window is per job type.** `reclaim_stale` requires its
  window to EXCEED the longest legitimate handler runtime or it reclaims a live job and runs it
  twice, concurrently with itself. A handler that outgrows the fleet default
  (`WORKER_STALE_JOB_SECONDS`) gets an entry in `job_queue.STALE_AFTER_OVERRIDES_SECONDS`,
  **never a bigger global number** (the global also governs how fast a genuinely dead worker's
  jobs come back). Setting the default asserts every unlisted type finishes inside it — the
  config comment names them. When a job exhausts `max_attempts` nothing re-enqueues it: the
  recovery surface is `GET /operator/v1/jobs/stuck-dedup` (fleet board, read) +
  `POST /operator/v1/tenants/{org}/fix/redrive-dedup` (session-gated, audited), and it is
  manual on purpose — stranded jobs fail for unrelated reasons that each need a look.
- **A REGISTERED JOB WITH NO ENQUEUE SITE IS AN INERT FEATURE, AND NOTHING REPORTS IT.**
  A job type with a worker lane, a reclaim-window decision, a handler and passing handler
  tests but no caller is never run, and every gate stays green because each half is correct.
  **When you add a job type, the same change must add its enqueue site, and a test must
  assert the caller calls it** — `tests/architecture/test_job_enqueue_reachability.py`
  requires a reachable enqueue site for every registered type, and
  `tests/services/test_bi_jobs.py` asserts both directions for the BI triggers (a succeeded
  build asks, a skipped build does not). Enqueue counts ride on the job's progress record so
  "queued nothing" is distinguishable from "was never asked".
  The same class hides elsewhere in a green suite: a guard whose rules were never proven
  able to fire, a stale route-count tripwire, a Postgres parity suite that iterates one model
  module while the tables live in others, and front-end surfaces whose routes work but are
  never called. **"The endpoint exists" is not "the feature works", and a green test suite is
  evidence about the code that was written, not about the code that was not.**

---

## 3c. Market Data Adapter framework (docs/market_data_adapter.md)

Layer-1 source adapters specialized for vendor market data, under
`backend/app/adapters/market_data/`. Calculation modules never learn the vendor: they consume
by `DataScope` + as-of + institution through `app/services/market_data.py`, and vendor concepts
(Bloomberg mnemonics, Refinitiv RICs, raw vendor errors) never cross the adapter boundary.

- **Canonical entities** (`app/models/canonical.py`, full mandatory-metadata mixin + RLS +
  current-generation supersession): `canonical_yield_curves` (+`_points`), `canonical_fx_rates`,
  `canonical_market_indices`, `canonical_counterparty_ratings`. Rates are decimal fractions
  (0.158, never 15.8).
- **`MarketDataAdapter(SourceAdapter)`** (`base.py`) with three shipped implementations, each
  passing one shared contract suite (`tests/adapters/market_data/contract.py`, §4.3 categories +
  a vendor-internal leak canary): `manual_upload` (production path — xlsx templates + parser +
  upload/template endpoints; the staged `temp://` handle is the "credential"; zero vendor quota),
  `refinitiv` (OAuth2 simulated, `ric_catalog.yaml`), `bloomberg` (enterprise-cert simulated,
  `field_catalog.yaml`). Catalogs carry ONLY spec-documented vendor identifiers; everything else
  is `supported: false` — never invent mnemonics/RICs. Live vendor transports are a Phase 2
  drop-in behind the `TokenProvider`/transport protocols; fixtures drive all testing.
- **One persistence spine** (`pull_runner.execute_pull`): batch + lineage
  (EXTRACT→TRANSLATE→VALIDATION) + raw-tier preservation
  (`market_data/{vendor}/{as_of}/{batch}/{scope}.json`, kept even for rejected pulls) +
  business-rule validation + canonical persistence with supersession (idempotent re-pulls) +
  quota accounting + canonical-tier cache + a debounced `pipeline_refresh` enqueue — so any
  market-data arrival auto-recomputes dependent modules and flips official-run freshness to
  stale.
- **Multi-source**: each source's series supersedes within itself; cross-source disagreement
  stays visible as parallel current rows, and reads arbitrate most-recent-refreshed-wins
  (spec §15; consensus is Phase 3). Every read view carries `SourceAttribution`
  (source_system, batch, ingested_at, stale, age) and fact derivation records the winning
  source in `attributes["derived_from"]`; stale usage is attributed, never silent.
- **Dual-curve selection** (curve platform spec §6 / §13 Stage 2): discounting is a separate
  selection from projection. `market_data.get_discount_curve` prefers the desk's
  `AEQ.{CCY}.OIS` (the AGD for GHS), else the latest current-generation
  `curve_type='discount'` curve, else returns None — and None means every consumer falls back
  to single-curve behavior, byte-identical to the historical runs (the hermetic seed publishes
  no desk curves). IRR EVE/duration PVs discount on the published curve when present (snapshot
  gains a `discount_curve_pct` block only then; hashes of unaffected banks never move), while
  floating legs keep repricing off the projection curve. Projection selection: fact derivation
  prefers the desk's `AEQ.{CCY}.SOV.ZERO` for the FTP/transfer base curve and falls back to
  currency-level arbitration, stamping the winner in `derived_from`; FTP pricing itself never
  reads the discount curve (transfer-curve carry is a funding cost, not a PV).
- **Credentials**: `EncryptedDbVault` (AES-256-GCM, key from `CREDENTIAL_VAULT_MASTER_KEY`,
  per-pull retrieve-and-discard, write-only at the API — responses carry only fingerprint,
  expiry, status). Lifecycle states per §10.2 with expiry-driven
  ACTIVE→EXPIRING_SOON→EXPIRED transitions on the scheduler tick. HashiCorp Vault is a
  drop-in behind the `CredentialVault` protocol later.
- **Scheduling**: `market_data_pull` jobs on the existing queue/worker; the hourly tick
  enqueues due pulls per connection schedule, gated on `MARKET_DATA_PULL_ENABLED` (default
  off). Quota is tracked per (bank, vendor, month) and estimated pre-pull; enforcement beyond
  warnings is Phase 2 (§16.5).

### Market-data standing rules and the research desk

- Market data flows only through `app/adapters/market_data/` (see §3c above and
  docs/market_data_adapter.md). Every adapter pull delegates to `pull_runner.execute_pull` —
  the single writer of market-data canonical state; never persist market data elsewhere.
  Vendor catalogs carry only spec-documented identifiers (`supported: false` otherwise —
  never invent Bloomberg mnemonics or RICs), and raw vendor errors/fields must never reach
  bank-facing surfaces (classify via `errors.BankFacingErrorCode`; the contract suite's
  leak canary enforces this). Vendor naming: the Refinitiv brand is retired (Eikon is
  LSEG Workspace; the platform APIs are the LSEG Data Platform, formerly RDP) — the
  internal vendor id stays `refinitiv` for wire/DB stability, user-facing labels read
  "LSEG (formerly Refinitiv)".
- Calculation modules consume market data ONLY via `app/services/market_data.py`
  (DataScope + as-of + institution, source attribution + staleness on every view);
  `fact_derivation` prefers canonical market-data entities and falls back to legacy
  `canonical_reference_rows`. Cross-source disagreement is resolved at read time
  (most-recent-refreshed wins) — supersession applies within a source series, not across
  vendors.
- Vendor credentials live only in `EncryptedDbVault` (AES-256-GCM,
  `CREDENTIAL_VAULT_MASTER_KEY`), retrieved per pull cycle and discarded; connection APIs are
  write-only for credential material (responses expose only fingerprint/expiry/status).
  Scheduled pulls are gated on `MARKET_DATA_PULL_ENABLED` (default off).
- **Market research desk** (spec `docs/internal/AequorOS_Market_Data_and_Curve_Platform.md`
  — its as-built header and calibration deviation are authoritative). Desk-as-vendor:
  approved determinations publish into EVERY tenant through `pull_runner.execute_pull` as
  vendor `aequor_desk` (zero quota, AEQ.* curve names so vendor rows coexist — supersession
  keys ignore source). Global `desk_*` tables: methodology register (Track-1 weekly
  application vs Track-2 versioned parameter changes, maker-checker everywhere), bitemporal
  determinations, silver captures. **Rates-first weekly flow:** `desk_capture` stages a
  pre-computed **draft** only (never auto-submits); Analyst reviews/adjusts then submits;
  Supervisor approves. Determination-scoped `research_adjustments` (override /
  additive_bps / assumption_note + rationale) enter `package_digest` and do not rewrite the
  methodology register. Split QA: `rates_qa_passed` gates approve/submit/publish of rates;
  `curves_qa_passed` is advisory for rates publish (curve scopes omitted when false). Quant
  lib `app/domain/curves/` is pure. Nightly job behind `DESK_CAPTURE_ENABLED`.
  **Entitlements (spec §10):** `market_data_entitlements` grants org × dataset (tiers
  core/standard/premium); default standard when no rows; publish + market-data reads filter
  AEQ curves / GHS indices accordingly. The credit curve `AEQ.GHS.CORP` is built from liquid
  GFIM corporate yields when present; true OIS uses methodology
  `discounting_mode=ois_bootstrap` + `GHS.OIS.*` (falls back to synthetic AGD). Capture
  snippet viewer: `GET .../captures/{id}/content`. Engines: `get_discount_curve` prefers
  AEQ.{ccy}.OIS — EVE/duration discount on it when published, byte-identical fallback
  otherwise (the golden suites prove the fallback; never edit goldens to make dual-curve
  changes fit).

---

## 3d. ICAAP workspace and the filing plane (backend/docs/icaap_workspace_and_filing.md)

The ICAAP workspace is the first surface that is a **document under review**
rather than a computed view: cycles, narrative sections, a stage/decision chain,
data blocks bound to computed evidence, and — at freeze — an immutable
regulatory package. It reuses the calculation-run pattern (§3) for every figure
and the live/governance boundary (§3b) absolutely: no ICAAP module may import
the live plane or re-derive facts, pinned by
`tests/architecture/test_icaap_boundaries.py`. A Board-approved figure is one
that was computed once, reviewed, and can be pointed at afterwards.

Four structural facts this plane adds, each of which has already cost a defect
when it was assumed away:

1. **There is a SECOND package-mint site.** `generate_frozen_package` is a PEER
   of `generate_package`, not a variant: the caller owns what is in the
   snapshot, the mint site owns what a package IS. Gates may live on either
   side; **neither side may drop one**. `freeze_cycle` runs the
   reporting-period and reconciliation gates the generic site runs, because
   they are not reachable from the frozen path otherwise.
2. **`family_hooks` is the one seam a return family may use.** Lazy
   `importlib` dispatch (the family package imports the generic plane, so a
   module-level import would close the cycle), a no-op default for every hook,
   and deliberately not `if package.return_family == "icaap"` in five services.
3. **The `ai` job lane exists.** `icaap_ai_draft`, `bi_commentary` and
   `bi_nlq_translate` are its members (`job_queue.JOB_LANES`), the
   default lane excludes them by construction, and
   `app/worker.py::resolve_job_types` refuses a process that mixes lanes. The
   reason is the model credential: the process holding `ANTHROPIC_API_KEY` runs
   nothing else, and it deploys from its own compose file.
4. **Jurisdiction identity is data under `app/domain/icaap/frameworks/<code>/`**
   with no `if jurisdiction ==` anywhere — the same rule §2 and the jurisdictions
   registry state, applied to a whole regulatory regime. Read the sourcing
   caveats in the linked document before relying on the Nigeria or Kenya
   framework: neither primary text is in the checkout.

The dispatch/calculation parameter boundary belongs here too: package
generation resolves every registry entry's `effective_from_parameter` to decide
which returns exist, so `PrefetchedParameterResolver.load()` **requires** an
explicit `record=` and the registry-driven sites pass `record=False`. Without
it, adding one registry entry moved an unrelated family's content digest.

---

## 3e. BI plane (governed analytics over the bank's own treasury and ALM data)

One governed foundation: worker-built marts and conformed dimensions, a metric
catalogue, a safe query compiler, and an authorization gate every BI surface
reads through. Built behind default-off flags (`Settings.bi`); no BI route is
mounted until `BI_ENABLED`.

**The plane boundary is the whole design.** BI is a DISPATCH plane: it reads
canonical rows (through `is_current_generation` only), `live_metrics`,
`regulatory_runs` and the registers, and it writes **the `bi_*` tables** (the
guard derives the writable set from the model registry by prefix, so a new mart
is covered on the day it is written) **plus `ai_commentary_drafts`** — the one
non-`bi_` table a BI-owned module writes, by decision D-191, from
`app/jobs/bi_commentary.py`. Stated precisely because audit A360-1 M1 found the
older "nothing else" wording false: the write scan then covered `app/services/bi`
and `app/domain/bi` only, while `app/jobs/bi_*.py` and `app/features/*bi*.py`
were exempt from the import rule and unscanned. The 2026-09-29 remediation makes
the exemption set and the scan set the SAME set and names `ai_commentary_drafts`
as the one permitted non-`bi_` write
(`test_every_bi_owned_module_is_in_the_write_scan`; in the working tree at the
time of writing). The regulatory plane never imports BI, with exactly two
exceptions —
`app/services/bi/enqueue.py` and `app/services/bi/versions.py`, the enqueue
seam the hook sites call, which themselves import no BI model, builder,
catalogue or compiler. `tests/architecture/test_bi_plane_boundary.py` pins all
of it, including that BI never calls `derive_facts`.

- **Engine metrics are COPIED, never recomputed.** `bi_fact_engine_metric`
  carries a `live` tier from `live_metrics` and an `official` tier from the
  latest succeeded baseline `regulatory_runs` per period, with the input hash,
  pipeline state and the live plane's own `reconciliation_blocked` flag that
  produced them (the regulatory plane's provenance about the source figure,
  copied as-is — not a BI verdict). A metric
  resolves to its authority by `(metric_id, regime)`; only `filed` designations
  may be badged certified (an SDI's IRRBB has no registry entry at all and is
  marked `unregistered`).
- **Portfolio measures reuse the engines' own pure functions** — loan
  classification, product family, DPD bands, repricing and ladder buckets, and
  the BSD7 P&L mapping, all lifted into `app/domain/` so there is one
  definition rather than a BI copy.
- **Two FX rules, declared per measure.** Classification counts an unconverted
  foreign-currency loan at zero; fact derivation excludes it. The mart carries
  both (`classification_exposure_rc` beside `balance_rc` + `fx_unconverted`) so
  portfolio NPL is the SAME figure as the engine's, and each measure declares
  which rule it follows.
- **BI carries NO reconciliation to the regulatory returns — founder decision
  2026-09-29.** Until that date this bullet read "Reconciliation R1–R12 drives a
  trust badge (`green | amber | red | grey`)": twelve checks in
  `app/services/bi/reconciliation.py` compared the marts with "the figures the
  platform already files" (R4 against BSD7A, a BoG return, line by line), and
  the verdict was rendered as a "Does not reconcile" chip on every dashboard
  card, treasury ones included. The founder rejected the premise — _"we are
  showing intelligence to users based on their data. Let's not complicate
  things"_: treasury/ALM and the regulatory spine are different planes, and BI
  is analytics over the bank's own treasury data. Removed by that decision: the
  R1–R12 checks and `bi_reconciliation_results`, `MeasureDef.reconciliation_checks`,
  `GET …/bi/trust` and the `trust` badge on every BI payload, the `TrustBadge` in
  `ChartFrame`, the Compliance pack's `reconciliation_trust` panel, the export
  metadata's "Data confidence" field and the feed's `X-Bi-Feed-Trust` header.
  **Build freshness STAYS** — `bi_mart_builds`, the value-based fingerprint and
  `provenance.stale_dates` say the bank's own data is stale, which is not a
  regulatory statement; the stale-date signal needs a surface of its own now
  that the badge that carried it is gone (audit A360 H2). Nothing on the
  regulatory side moved: filing gates, ICAAP freeze gates, BoG return
  reconciliation and `derive_facts`' refusal are untouched. Never put a
  regulatory verdict back on a BI surface. The spec that produced the mistake,
  `docs/bi.md`, is gitignored (`.gitignore:62`) and so not reviewable in the
  repository — a recorded governance gap.

**Partition RLS is the sharp edge.** Postgres does not inherit row-level
security onto partitions, so the marts' monthly and yearly children are created
and dropped only by migration-owned `SECURITY DEFINER` functions
(`bi_ensure_month_partition` and siblings) that apply `ENABLE`+`FORCE` RLS and
the tenant policy to every child, pin `search_path` and UTC bounds, and refuse a
parent of the wrong cadence. The app role owns none of them. A cross-tenant read
returns zero rows through the parent, a child and the DEFAULT partition alike,
and zero with no GUC set.

**The compiler never assembles SQL.** `BiQuery` is a closed pydantic schema;
every member resolves to a mapped column; filters become bound parameters under
a whitelisted operator set; grouping, pivot and subtotals are computed
server-side. There is no `text()` anywhere in `app/services/bi` or
`app/domain/bi`. The executor alone may call `exec_driver_sql` with two named
constant statements that set the statement timeout and read-only flag; the
timeout is a bound parameter. This boundary is pinned by
`tests/architecture/test_bi_compiler_injection.py`, which proves its AST guard
against deliberate violations and fuzzes client input with Hypothesis.

**BI gets its own worker lane** (`risk-worker-bi`, `WORKER_JOB_TYPES=lane:bi`)
because `claim_next` is FIFO across types and a backfill would otherwise starve
`pipeline_refresh`. Backfill is ONE self-re-enqueuing cursor job, bounded and
refusing to advance backwards. Every payload carries `builder_version`; an older
handler marks a newer job succeeded with `progress={"status":"skipped",...}`.

### Phase 2 additions

- **Targets are PRE-MATCHED, not joined.** `bi_fact_target` holds the bank's own
  budget or reforecast beside the actual and the variance, one row per (as-of,
  measure, scope, version), so all five catalogue variants read one table and the
  compiler's one-fact-table rule holds unrelaxed. A target joined at query time
  would fan out — a bank-wide target multiplied across the scoped rows of the
  same measure double-counts, and the double count is invisible because both
  operands are legitimate figures. A measure with no target has **no row**, which
  is what makes its variants NULL rather than zero; the three discriminator
  columns are POPULATION filters for the same reason (as selection filters the
  compiler emits `sum(CASE WHEN … ELSE 0 END)`, so a bank that budgets its loan
  book but not its NPL ratio would read `0.00` — a bank exactly on plan).
  `attainment_pct` is not emitted where lower is better: 120 % on an NPL ratio is
  a miss and reads as over-achievement away from its badge.
- **Content packs are data, validated at import.** `app/domain/bi/packs/<id>.json`
  under a pydantic `PackSpec`; a malformed pack fails module import, so a bad pack
  is a start-up failure rather than a 500 in front of a board. **A pack carries no
  date** — `BiPackQuery.for_period(as_of)` resolves it, because a pinned reporting
  date is the one parameter that must come from the bank being served. A widget
  with no data says `needs_data: <dataset>`; a widget blocked on PLATFORM work
  says `pending_capability` instead, because "Needs data: positions" to a bank
  that pushes positions nightly is a false statement about its book. Never zero.
- **An insight may only restate a typed fact.** There is no path from a query
  result to a sentence that does not pass through `insights/facts.py`, so an
  insight cannot assert a figure the platform did not compute, describe a missing
  figure as zero or flat, or present an advisory number as certified. The ratio bridge sums exactly or returns `BridgeUnavailable` with a
  reason — never a leg worth nothing.
- **Export authority is derived from the member set, never from a client flag.**
  `authorization.query_members` gives the transitive walk and each member declares
  its sensitivity; the class is re-checked after compilation from
  `CompiledQuery.member_ids`, and that second check can only refuse. So the export
  path cannot serve a member the read routes would refuse, and a principal who may
  run a query interactively is not thereby allowed to extract it. Every export is
  audited and watermarked; over the interactive threshold it becomes a `bi_export`
  job that re-authorizes at render time rather than trusting the request's
  authority.
- **`bi_query_log` names every surface it records**, including `catalogue`,
  `packs` and `insights` — surfaces that return no mart rows or that make a
  statement rather than return a figure (and, historically, `trust`, the surface
  removed by the 2026-09-29 decision; the log is append-only, so migration
  `202609290080` keeps the value in the CHECK — an append-only table's
  vocabulary can only grow). The read budget is counted over
  this table, so a surface with no word of its own either goes unmetered or is
  recorded as something it is not. Its downgrade path deliberately FAILS rather
  than deleting rows: an append-only log a migration can quietly empty is not one.

### Phase 3 additions

- **Self-service reads through the same compiler.** Explore is AG Grid Community
  over the existing grid endpoint, so row groups, pivot and subtotals are computed
  SERVER-SIDE and nothing is re-aggregated in the browser. Community has no
  server-side row model, hence the infinite row model, and the page size is
  discovered from the server's own cap rather than chosen. The grid's built-in
  export is disabled and asserted off three ways: it bypasses authorization, the
  audit record and the watermark, so it is a data-egress path rather than a
  convenience.
- **A calculated measure is authorized as THE FIGURES ITS TEXT NAMES.** Never as
  itself: a formula is a bank's own arithmetic over catalogue members, so the
  sentence a reader must hold is the union of the sentences those members need.
  The authorization walk expands it from the server's own re-parse of the APPROVED
  text every time and never reads the stored member column, so doctoring that
  column cannot widen the walk, and an id the expansion cannot resolve is left
  alone so it still refuses. Only certified measures load, so a draft is a generic
  unknown-id refusal rather than a named one.
  **The grain is declared in the formula text** (D-195), which is what a checker
  approves and what the digest covers, and there are exactly three grains because
  the date dimension carries exactly three flags — a weekly grain would have to
  invent where the previous period ends. `LAG`'s reach derives from the daily
  retention window (D-196) divided by the longest a period can be, so a formula
  cannot be answerable in February and empty in March.
- **Maker-checker is enforced beneath the service, not only by it.** Two CHECK
  constraints make a self-approved promotion and a promotion whose expression has
  moved since approval UNSTORABLE. Separation of duties that lives only in a
  service is one code path away from being bypassed.
- **Sharing never carries the owner's authority, and neither does a delivery.**
  The widget resolver is not given the owner — there is no parameter it could
  consult — and a subscription renders once PER RECIPIENT under that recipient's
  own access, which is asserted by the recipient's name appearing in the
  artifact's own provenance bytes. Confidential content becomes a sign-in link
  rather than an attachment, decided by the same export classifier before a row is
  read.
- **A dashboard's version history is append-only in the database**, the same three
  ways the audit log and the query log are. It is evidence of what a reader was
  shown on a date, so rewriting it is refused rather than merely avoided.
- **Alerts and on-new-data reports are triggered by a SUCCEEDED mart build, in the
  job handler and not in the builder.** The builder is called once per date by the
  backfill, so a hook inside it would mail a bank a thousand board packs; and it
  returns `skipped` when a fingerprint has not moved, which is what makes both
  triggers idempotent without a second mechanism. This is worth stating because
  the absence of that one call left both features inert — registered, handled,
  tested and never invoked — in a way nothing reported.

### Phase 4 and Phase 5 additions (2026-09-27..28)

- **Binding data scope** is governed by the
  [whole-institution contract](backend/docs/authorization_foundation.md#whole-institution-figures-and-credit-only-narrowing).
  [BI scope reduction and compiler injection](backend/docs/bi_enforcement_rollout.md#branch-and-region-scope-is-enforced-desk-and-currency-are-not)
  and [Credit row filtering](backend/docs/credit_enforcement_rollout.md)
  describe the row-filtered surfaces; `/auth/me` projects scope per capability.
- **`bi_reader` is the second machine bundle** (`{view}`, disjoint from
  `integration_writer`'s `{ingest}`, machine-only by CHECK; `202609270074`),
  minted with an integration key (`purpose=reader`) for the **Power BI Stage B
  feed** — `GET …/bi/feeds/{dataset}`, curated aggregated datasets only, NDJSON
  or CSV, a cursor anchored on the MART BUILD rather than the business date so
  a restated month re-arrives, every pull logged (`backend/docs/powerbi_stage_b.md`).
  Stage A (governed file exports into Power BI Desktop) is
  `backend/docs/powerbi_stage_a.md`.
- **Credit routes moved onto scoped bindings** (`app/api/deps.py::require_credit_*`,
  per-route sensitivity), and the blotter, facets and activity grid apply the
  reader's data scope with every count computed AFTER the filter.
- **Two more marts.** `bi_fact_gl_branch_monthly` (`202609280075`) is the branch
  ledger from the NEW `gl_segment_balances` reference dataset — a separate table
  rather than a `branch_code` column on the institution ledger, because
  `authorization.branch_attributable` reads the branch key off the TABLE and a
  branch key on the institution ledger would make the institution's own P&L
  readable by a branch-scoped principal (argument: `docs/data_engine/datasets/gl_segment_balances.md`).
  Four optional position columns (`officer_id`, `channel`, `account_status`,
  `arrears_amount`; `202609280076`) land on both position facts; a catalogue
  member binds to a mart COLUMN, so an ingestion-only field is not analysable.
- **Natural-language questions** (`202609280077` adds the `nlq` log surface).
  The model emits a `BiQuery` over the members the reader can already see, never
  SQL; the proposal is shown, confirmed by digest (a changed or re-ordered
  proposal is 409) and logged; `bi_nlq_translate` runs on the exclusive `ai`
  lane beside `icaap_ai_draft` and `bi_commentary`. **Consent text amended
  2026-09-29** (`ai-consent-2026-09-v2`, the shipped default): it describes the
  question surface in its own section — what is sent (the typed question plus a
  catalogue of permitted figure NAMES), what never is (any figure, any answer,
  anything the asker may not see), and, stated plainly, that the screening cannot
  catch a customer name it has never been told. `bi_nlq` is consequently in
  `CONSENT_COVERED_FEATURES`. The rule that made this necessary is now ENFORCED
  rather than assumed: `gates.evaluate` refuses any feature the shipped consent
  text does not describe (`consent_not_covered`), at BOTH enqueue and run, so a
  database row cannot out-rank the document (audit A360-5 M2). Raising the
  version switches AI assistance off until each Owner accepts the new text.
- **One chart library.** Every Recharts importer in `backend/dashboard` was
  converted to ECharts (39 files at the time; 0 remain) and the dependency left
  the dashboard manifest; the staff `console/` still imports it in 3 files and
  is outside the BI spec's scope.
- **Six flags, all six projected.** `BiSettings` carries `BI_ENABLED`,
  `BI_MART_ENQUEUE_ENABLED`, `BI_SCHEDULER_ENABLED`, `BI_ALERTS_ENABLED`,
  `BI_SUBSCRIPTIONS_ENABLED`, `BI_NLQ_ENABLED`, all default off;
  `GET /feature-flags` serves all six. The alert and subscription flags were
  added to the projection by audit A360-2 M3: without them the screen said
  "Waiting for figures" about a shut flag, blaming the bank's data for a
  deployment decision. The production
  turn-on order is `backend/docs/bi_turn_on_runbook.md`.

### BI working rules

- **BI plane** (spec `docs/bi.md`, which is not published; working ledger in the gitignored
  `.ai/BI_*.md`). Governed analytics over the bank's own treasury data, so a bank can drop
  its separate Power BI project. It is a **DISPATCH plane**: it reads canonical rows
  (current generation only), `live_metrics`, `regulatory_runs` and the registers, and writes
  **the `bi_*` tables plus `ai_commentary_drafts`** (from `app/jobs/bi_commentary.py`; the
  write guard scans every BI-owned module and names that one permitted write); the
  regulatory plane never imports BI except the two enqueue-seam modules
  (`services/bi/enqueue.py`, `services/bi/versions.py`), which import no BI model, builder,
  catalogue or compiler. `tests/architecture/test_bi_plane_boundary.py` pins it,
  `derive_facts` included. **Engine metrics are COPIED, never recomputed**, and portfolio
  measures reuse the engines' own pure functions out of `app/domain/` — one definition, not
  a BI copy.
- **Nothing is mounted by default:** all six `BiSettings` booleans (`BI_ENABLED`,
  `BI_MART_ENQUEUE_ENABLED`, `BI_SCHEDULER_ENABLED`, `BI_ALERTS_ENABLED`,
  `BI_SUBSCRIPTIONS_ENABLED`, `BI_NLQ_ENABLED`) default off, so every BI route 404s until
  `BI_ENABLED` is set; `GET /feature-flags` projects all six. Turning it on is the ORDERED
  sequence in `backend/docs/bi_turn_on_runbook.md`, and `risk-worker-bi` (the `bi`-lane job
  types) must be DEPLOYED before the enqueue flag flips or every job it produces strands in
  `queued` (the shared `jobs` table hazard). **`CATALOGUE_VERSION` and `BUILDER_VERSION` both
  enter the build fingerprint**, so bumping either forces a full mart rebuild per tenant —
  bump deliberately.
- **Postgres does not inherit RLS onto partitions.** The marts' monthly and yearly children
  are created and dropped ONLY by migration-owned `SECURITY DEFINER` functions
  (`bi_ensure_month_partition` and siblings) that apply ENABLE+FORCE RLS and the tenant
  policy to every child; the app role runs no raw `CREATE TABLE`. A cross-tenant read must
  return zero rows through the parent, a named child and the DEFAULT partition alike. **A
  green Postgres run is not evidence of this** — an RLS test self-skips at exit 0 when the
  `TEST_DATABASE_URL` role bypasses RLS or when `TEST_DATABASE_URL` is not exported, and
  privilege proofs need `CREATEROLE`. Check that the intended RLS and privilege proofs
  actually ran. `risk-service:test-postgres-schema` provisions a local `NOBYPASSRLS`
  test role with `CREATEROLE` and requires the privilege proofs to run; a supplied URL
  must provide equivalent privileges. See [test database setup](backend/README.md#run-tests).
- **Missing data is never zero, structurally.** A measure with no target has no
  `bi_fact_target` row; a widget with no data says `needs_data: <dataset>` (or
  `pending_capability` when the gap is platform work, not the bank's book); an insight may
  only restate a typed fact, so it cannot describe a missing figure as flat; the ratio
  bridge refuses rather than emitting a leg worth nothing. Never "fix" one of these by
  defaulting to 0 — that is the defect they exist to prevent.
- **A filter can itself disclose**, so `authorize_query` evaluates every distinct (module,
  sensitivity) across measures, dimensions AND filters, deny-by-default, and export
  authority is derived from the member set the query touches — never a client flag, and
  re-checked after compilation where the second check can only refuse.
- **No `text()` in `app/services/bi` or `app/domain/bi`** (an AST guard that proves
  itself), no currency/regulator literal in BI code or pack JSON — though `eve_base_ghs` and
  `ghs_millions` are load-bearing wire keys, not leaks, exactly like the `bog_` fact
  categories and the `refinitiv` vendor id.
- **The packs name `.crd.official` measures and are correct for a BANK only** — an SDI has a
  different capital regime and is refused the packs surface until its own pack set ships
  (the gate resolves through the authority registry and opens by itself).
- **BI tables span three model modules** (`app/models/bi.py`, `bi_content.py`,
  `bi_notifications.py`), and three registries each convict you by name if you miss a new
  table: the plane guard's `BI_OWNED` globs, its writable-table derivation, and the table
  census that requires every `bi_*` table to be named by exactly one module's tuple. **Check
  which Postgres suite iterates your table** — `tests/db/test_bi_foundation_migration.py`
  reads `app/models/bi.py` ALONE, and `tests/db/test_bi_phase3_migration.py` gives the other
  modules' tables column, CHECK and FORCE-RLS parity. Without that parity a column too narrow
  for the values copied into it passes on SQLite (which ignores VARCHAR lengths) and fails a
  tenant's WHOLE nightly build on Postgres, because the model and the migration agree on the
  wrong number.
- A shared dashboard carries **no** owner authority (the widget resolver is not given the
  owner), and a subscription delivery is rendered **as each recipient**, asserted by the
  recipient's name appearing in the artifact's own provenance bytes. A range query over a
  stock measure carries a REDUNDANT static window bound beside its subquery — do not
  "simplify" it away: Postgres prunes partitions at plan time and cannot see a subquery, so
  without it a twelve-month question scans every month the mart holds.
- **A CALCULATED MEASURE IS AUTHORIZED AS THE FIGURES ITS TEXT NAMES, never as itself** — the
  walk re-parses the APPROVED expression server-side every time and never reads the stored
  member column. **The alert and on-new-data triggers live in the `bi_mart_refresh` HANDLER,
  not in `refresh_bank_as_of`**: the backfill calls the builder once per date, so a hook
  inside it would mail a bank a thousand board packs, and the builder's `skipped` outcome is
  what makes both triggers idempotent.
- **A binding is a five-dimension sentence.** Data scope is REAL:
  `authorization_bindings.data_scope_kind/values`, reduced PER CAPABILITY by
  `authorization.reduce_data_scope` — never union ids matched against different resources
  (`services/bi/authorization.py` and `services/bi/feeds/authorization.py` both go through
  the shared `combine_pair_scopes`) — resolved by `services/bi/data_scope.py` and injected
  BESIDE the `BiQuery` so no client can remove it; `scripts/authorization_access_impact.py`
  reports it (`data scope` column, `scoped_reader` flag) and is the gate for any change that
  touches it. `bi_reader` is the second machine bundle (`{view}`, disjoint from
  `integration_writer`'s `{ingest}`) for the Stage B feed (`backend/docs/powerbi_stage_b.md`);
  Stage A is `powerbi_stage_a.md`.
- **BI carries NO reconciliation to the regulatory returns:** treasury/ALM and the
  regulatory spine are different planes, and BI is intelligence over the bank's own treasury
  data. There are no regulatory reconciliation checks, no trust badge on BI payloads or
  cards, no export "Data confidence" field and no `X-Bi-Feed-Trust` header. **Build FRESHNESS
  stays** (`bi_mart_builds`, fingerprints, `provenance.stale_dates`) — a stale build is about
  the bank's data, not a regulator — and the stale-date signal needs a surface of its own
  (audit A360 H2). Never put a regulatory verdict on a BI surface.
- **Natural-language questions** (`ask` routes, `bi_nlq_translate` on the `ai` lane): the
  shipped consent text (`ai-consent-2026-09-v2`) describes the question surface, so `bi_nlq`
  is in `CONSENT_COVERED_FEATURES`. **The rule is enforced at the EGRESS gate, not in a
  request schema**: `gates.evaluate` refuses any feature the shipped consent text does not
  describe, at enqueue AND run, so a settings row written by any other path cannot out-rank
  the document. Adding a feature to that tuple without a consent section covering it is the
  defect. Reaching a tenant also needs the deployment flags, `risk-worker-ai`, a non-empty
  `approved_configurations.json` and the Owner's consent.
- `backend/dashboard` charts with ECharts and has no Recharts importer (`console/` still
  imports Recharts and is outside the BI scope). When a BI document and the code disagree,
  check the dated as-built notes in `docs/bi.md` before trusting a mechanism the prose
  describes — the code wins.

---

## 4. Findings infrastructure

Generic, reusable workflow — verified in `app/models/risk.py` and `app/services/findings.py`:

- `risk_findings` (`RiskFinding`): tenant + case scoped; `risk_type` (allow-list in
  `app/domain/risk_constants.py::RISK_TYPES`), `severity` (`low|medium|high|critical`), `status`
  (`open|accepted|acknowledged|dismissed|needs_review|resolved|superseded`), `source`
  (`deterministic_rule|manual|imported`), `rule_id`, `rule_version`, free-form `details` JSON.
- `risk_finding_evidence` (`RiskFindingEvidence`): per-finding evidence rows with optional
  document/chunk references and a free-form `locator` JSON (source_type, label, `source_url`
  deep link, record ids, `input_hash`).
- Service helpers in `app/services/findings.py`: `get_finding_or_404`, `list_findings`,
  `list_case_findings`, `create_case_finding`, `update_finding` / `apply_finding_update`
  (validates status transitions, requires disposition reason for dismissal, stamps
  `reviewed_by`/`reviewed_at` into `details`, emits `finding.status_changed`),
  `is_liquidity_workflow_finding`, `list_finding_evidence`.

How `app/services/liquidity.py` publishes findings (the template for new engines):

1. `calculate_metrics(periods)` — pure, deterministic; returns metrics plus a list of "concern"
   dicts (rule_id, severity, title, summary, rationale, affected periods, metric keys).
2. `generate_findings(db, ctx, run, periods, ...)` — takes the advisory publication lock, upserts
   the `LiquidityAnalysisResult`, marks prior `open`/`needs_review` findings for the same
   scenario as `superseded` (reviewed findings are never touched), then creates one
   `RiskFinding` per concern with `source="deterministic_rule"`, `rule_id`, `rule_version`, and
   `details={"liquidity": {workflow_id, rule_version, calculation_run_id, scenario_id,
input_hash, metrics}}`, plus `RiskFindingEvidence` rows for each forecast period, canonical
   input record, and scenario assumption — every locator carries the run's `input_hash` and a
   case-workspace deep-link `source_url`.
3. Workflow findings are protected from the generic `PATCH /api/v1/findings/{finding_id}`
   endpoint (`allow_liquidity_workflow` flag); reviews go through the dedicated
   `/liquidity/findings/{finding_id}/review` route.
4. The case-based SPA rendered any of these through a shared `FindingReviewCard`.
   That package (`apps/aequoros-web`) has been **removed** — the pattern is
   recorded here for the bank-scoped vertical; see git history for the source.

---

## 5. DECISION RECORD — Legacy case vertical vs. new bank-scoped regulatory vertical

**Status: accepted for this build (2026-07). This section is a forward-looking decision, not a
description of existing tables.**

- The case-scoped credit-review vertical — `risk_cases`, documents/extractions, the financial
  workspace (`financial_*` tables), case scenarios (`risk_scenarios`, `scenario_assumptions`),
  and case-scoped `calculation_runs` / `capital_projections` / liquidity analysis — is **LEGACY**
  as of this build. It stays in place: existing features keep working, its tests keep passing,
  and its _patterns_ (tenancy, immutable runs, findings, audit) are the blueprint for new work.
- The new ALM/regulatory vertical is **bank-scoped, not case-scoped**. New tables for this build:
  `banks`, `bank_reporting_periods`, `bank_financial_facts`, effective-dated `param_*` tables
  (runoff rates, ASF/RSF weights, risk weights, thresholds, stress shocks — versioned with
  `effective_from`/`effective_to`, jurisdiction, and approval metadata per
  `IMPLEMENTATION_APPROACH.md` §5.6), and `regulatory_runs` following the calculation-run
  pattern of section 3.
- **New modules MUST NOT add dependencies on `risk_cases`** (no FKs, no `case_id` columns, no
  case-scoped routes). Case tables are retained but deprecated for regulatory flows.
- New API namespace: `/api/v1/banks/{bank_id}/...` (same tenancy deps, same composite-FK pattern
  with `organization_id`, same RLS migration treatment).
- LCR/NSFR, Basel RWA/capital-ratio, and stress engines belong to the new vertical and consume
  bank facts + `param_*` rows, never financial-workspace case records.

---

## 6. OpenAPI contract flow

Verified in `backend/mise.toml`, root `mise.toml`, and `.pre-commit-config.yaml`.

1. Backend routes/schemas change → regenerate:
   `mise run risk-service:openapi-client`. This exports `openapi-schema.json` from the FastAPI
   app, regenerates `packages/risk-service-api` with openapi-generator (typescript-fetch,
   `supportsES6`), restores the source-first `package.json`, and runs Prettier over the generated
   sources (generation intentionally bypasses the repo formatting exclusion to keep output
   deterministic).
2. Validate the generated package: `pnpm --filter @aequoros/risk-service-api test` (compiles and
   runs `tests/generated-contracts.test.js`) and `type-check`.
3. **Freshness gate**: `mise run risk-service:api-fresh` regenerates, type-checks, then asserts
   `git status --porcelain` is clean for `backend/openapi-schema.json` and
   `packages/risk-service-api`. It runs on pre-push. A schema change without a committed
   regenerated client fails the gate.
4. `packages/risk-service-api/src` is excluded from style linting/formatting centrally; generated
   files must contain no inline suppressions. Type-checking and package tests remain required.
5. The web app must consume the generated client only — import types and
   `FromJSON`/`ToJSON`/`*Api` classes from `@aequoros/risk-service-api`; never hand-roll payload
   shapes (see CODEBASE_CONVENTIONS for the two sanctioned wrapper patterns).

### Generated-client hazards

- Regenerate scenario and other API contracts with
  `mise run risk-service:openapi-client`; validate the generated package with
  `pnpm --filter @aequoros/risk-service-api test`.
- **Regenerating while a `next dev` server is up poisons it — restart the
  dashboard.** The task DELETES and rewrites `packages/risk-service-api/src`, so a
  running dev server reading `src/index.ts` mid-rewrite caches the failure and then
  serves **404 for every `/_next/static/chunks/*`** while still returning 200 for the
  HTML. The symptom is a WHITE PAGE with no console error worth the name, and it does
  not self-heal on reload. Fix: `rm -rf backend/dashboard/.next` and restart the dev
  server.
- **Two schema shapes break generation itself:** two Pydantic classes sharing a NAME
  across modules (FastAPI then emits `app__schemas__x__Name` component keys the
  generator cannot map back), and a `Decimal` form field (Pydantic types it
  `number | string`, and the alias lands in an operation request interface, not in
  `src/models/`).
- **A STALE GENERATED CLIENT DROPS A NEW REQUEST FIELD SILENTLY, AND THE SERVER THEN
  DEFAULTS IT.** The two directions degrade differently, and only one of them is
  visible. `<Model>FromJSON` opens with `...json`, so an unknown RESPONSE field
  survives under its snake_case wire name — a frontend reading the camelCase property
  gets `undefined`, which surfaces as an obvious bug. `<Model>ToJSON` has **no spread**:
  it returns a hand-enumerated object literal of exactly the keys the generator knew
  about, so a REQUEST field added to an existing schema is stripped in the browser, the
  server applies its column default, and the API answers 201. That is not a type error
  and no frontend test sees it. For example, posting a `ScopedGrantInput` whose
  `data_scope_kind` / `data_scope_values` the client does not know through the
  generated `authorizationApi` stores a grant an Org Owner narrowed to two branches as
  **the whole institution, with a success dialog** — privilege widening reported as
  success. So after adding a field to a schema an existing route already accepts,
  either regenerate before the surface ships, or post through a hand-written transport
  that reuses the generated `FromJSON` parsers plus `normalizeApiError`, and pin a test
  that FAILS if the generated write operation is called again. Delete the transport at
  regeneration; an interim one that outlives it is a second contract nobody is checking.
  **When you delete it, invert the tripwire rather than dropping it:** the test that
  forbade the generated operation becomes one asserting the transport is gone AND that
  every field the generated serializer emits is one the caller states — a field the
  contract carries and the caller leaves unset is still decided by the server's column
  default, so the same widening returns the next time the schema grows. Give the
  builders the generated request-model TYPES; then the compiler checks the shape
  instead of a second literal.
- **Not every hand-written transport is the interim kind.** `lib/api/askTransport.ts`
  is permanent; deleting it breaks the feature. The test is which way the serializer
  hurts you. An INTERIM transport exists because the client does not yet know a FIELD,
  and a fresh client retires it. A PERMANENT one exists because a value must travel
  **unmodified** through a layer that rewrites every value it understands, and no
  generation changes that: BI's confirm-what-you-were-shown contract digests the
  proposal on both sides, `ToJSON` drops what it does not know (digest mismatch) and
  `FromJSON` spreads the raw JSON and then re-adds known fields under camelCase (so
  `top_n` returns as `top_n` AND `topN`, and `BiQuery` is `extra="forbid"` — a 422 on a
  question the reader confirmed). Carry such a value as opaque JSON end to end, and pin
  BOTH directions against the generated package's own source.

---

## 7. Cash-flow ML module (`backend/app/ml`)

**Status: built and folded into the backend (originally a standalone `backend/app/ml`
sidecar; merged 2026-07 so all seven capability modules live in one deployable).**

- `backend/app/ml`: PyTorch LSTM + static-baseline cash-flow forecasting as an internal
  package of the risk service — `synthetic.py` (deterministic demo series), `features.py`
  (calendar features), `baseline.py`, `model.py` (train/persist/forecast), `config.py`
  (`TrainingConfig`, model version).
- Endpoints (`/banks/{id}/cashflow-forecast`, `/banks/{id}/cashflow-history`) enforce tenant
  scoping (verified bearer credential → `TenantContext` → bank ownership) in
  `app/services/cashflow_forecast.py`, which lazy-trains on first forecast (or loads saved
  artifacts) via an in-process `ForecastService` singleton. The ML package itself is
  tenant-unaware compute; the service layer owns authorization and response shaping.
- Settings live in `app/core/config.py` (`CashflowSettings`): `CASHFLOW_ARTIFACTS_DIR`
  (default `backend/artifacts/cashflow`, gitignored) and `CASHFLOW_FAST_TEST=1` for the
  reduced test-training config. There is no ML base URL — nothing to proxy to.
- torch is imported lazily on first forecast; if the ML runtime fails to load, the forecast
  endpoints return 503 (same contract as the old sidecar-down path) instead of failing the
  whole service. History needs no torch.
- ML inference results that feed decisions should be persisted through the section-3 run pattern
  (snapshot, hash, versions, findings) like any other engine.

---

## 8. Validation commands

| Target                   | Commands                                                                                                                                                                                                                                                                              |
| ------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| risk-service (all)       | `cd backend && uv run pytest` · `uv run ruff check .` · `uv run basedpyright` — or one shot: `mise run risk-service:check`                                                                                                                                                            |
| risk-service vs Postgres | `mise run risk-service:test-postgres` (reuses `TEST_DATABASE_URL` or provisions an isolated local service); see the [local-service guide](backend/dashboard/README.md#local-services-without-docker-or-orbstack)                                                                      |
| risk-service migrations  | `mise run risk-service:migrate` (needs `DATABASE_URL`); new revision: `mise run risk-service:revision "message"`                                                                                                                                                                      |
| dashboard                | `pnpm --filter @aequoros/dashboard typecheck` · `lint` · `test` · `build` · `e2e` (production build includes the [bundle deferral guard](backend/dashboard/README.md#nextjs-16-runtime-conventions); see [dashboard E2E guidance](backend/dashboard/README.md#end-to-end-playwright) for storage prerequisites) |
| marketing                | `pnpm --filter @aequoros/frontend lint` · `build`                                                                                                                                                                                                                                     |
| operator console         | `pnpm --filter @aequoros/console typecheck` · `lint` · `test` · `build`                                                                                                                                                                                                               |
| generated client         | `pnpm --filter @aequoros/risk-service-api test` (and `type-check`)                                                                                                                                                                                                                    |
| client regen + freshness | `mise run risk-service:openapi-client` then `mise run risk-service:api-fresh` (must leave git clean)                                                                                                                                                                                  |

All `mise run risk-service:*` tasks work from the repo root or from `backend`.

**CI coverage is listed below; console lint remains a local check** (2026-08-22 — before that, `frontend/` and `console/`
appeared in no workflow at all, and the dashboard workflow ran neither `lint` nor `test`, so
the regulatory fail-open guard and the browser-runtime SSRF guard were unenforced):

| Workflow                                   | Jobs                                                                                                                           |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------ |
| `.github/workflows/risk-service.yml`       | `static` · `architecture` · `postgres` · `postgres-suite` (with OpenBao) · `storage` · `api-fresh` · `real-data` (conditional) |
| `.github/workflows/dashboard.yml`          | `dashboard` — client typecheck, dashboard typecheck, lint, test, production build + Command Center entry-graph guard           |
| `.github/workflows/web.yml`                | `frontend` — lint, build · `console` — typecheck, test, build                                                                  |
| `.github/workflows/dashboard-journeys.yml` | `journeys` — manual dispatch; see [dashboard E2E guidance](backend/dashboard/README.md#end-to-end-playwright)                  |

Each workflow carries its own gate inventory in a header comment; keep it accurate when you
add a step.

The hermetic SQLite suite remains available to developers as `mise run risk-service:test`, but
CI no longer exercises SQLite compatibility. Postgres suite ownership is defined in the
task comments in [backend/mise.toml](backend/mise.toml) and pinned by
[the CI wiring guard](backend/tests/architecture/test_ci_task_wiring.py).

### CI enforcement

- **CI enforces every surface.** `risk-service.yml` gates the backend, `dashboard.yml`
  gates typecheck + **lint** + **test** + build, `web.yml` gates `frontend` lint+build
  and `console` typecheck+test+build, and the manual-dispatch `dashboard-journeys.yml`
  runs the dashboard Playwright journeys against disposable MinIO. See
  [dashboard E2E guidance](backend/dashboard/README.md#end-to-end-playwright) for the
  reasoned, size-pinned quarantine and the canonical fixture carried forward through the
  last month end on or before today. These gates are what enforce the dashboard's
  fail-open guard, SSRF egress guard, and browser journeys, and they cover `frontend/`
  and `console/`. Each workflow's header comment is its gate inventory — keep it
  accurate. Which Postgres job owns which test suites is defined by the
  `risk-service:test-postgres-*` task comments in `backend/mise.toml` and pinned by
  `tests/architecture/test_ci_task_wiring.py`.

---

## Structure decision (2026-07 — six-module completion)

A directive proposed relocating the Python backend into `dashboard` and deleting
`frontend` / `aequoros-web`. This was **declined** as based on a misread of the layout:
the backend was already cleanly consolidated in `backend` (FastAPI) and
`backend/app/ml` (LSTM) — since flattened into a single `backend/` service with the LSTM
as the in-process `app/ml` module. Moving a Python/uv/alembic service inside a Next.js/pnpm package
would break the workspace, migrations, RLS, the OpenAPI client-gen pipeline, and the test
suite. The monorepo was kept intact; nothing was moved or deleted.

**`dashboard` is the primary product surface** — the Bank Treasurer console, wired
end-to-end to the risk-service via the generated `@aequoros/risk-service-api` client, with
zero hardcoded financial data. `frontend` (marketing) is the other independent
deliverable; `apps/aequoros-web` (the case-based SPA) was **removed** — see git history.

### Six regulatory modules (all built, DB-driven, tenant-scoped)

Seven rows below: the cash-flow LSTM is a sub-module of liquidity, not a seventh
regulatory module. "Built" means computed server-side from ingested data with an
immutable run — it does not mean any figure has been filed with a regulator.

Each follows the same pattern: pure Decimal engine in `app/domain/<module>/engine.py`,
immutable `RegulatoryRun` persistence (snapshot + SHA-256 hash + versioned metrics/line-items/
validations), bank + reporting-period scoping, effective-dated `param_*` inputs, and a
`get_<module>_dashboard` with stored-run-first + inline-fallback.

The five detailed regulatory dashboards (liquidity, capital, IRR, FX, and FTP) batch that
fallback path per request. They load candidate succeeded baseline runs and financial facts
once, then reuse effective-dated tenant parameters, governed policy generations, market-data
curves, and SDI Net Own Funds from request-local, fully scoped collections. Every collection
is constrained by the applicable organization, institution, jurisdiction or policy scope,
market-data scope, and effective date; none is shared across requests or tenants. The 13-point
trend therefore has effectively flat query growth while retaining stored-run precedence and
the existing calculation engines. Audit-style full-HTTP query counts fell from 331 to 14
(liquidity), 494 to 15 (capital), 164 to 16 (IRR), and 73 to 12 for both FX and FTP.
`backend/tests/services/test_regulatory_dashboard_query_shape.py` pins those counts, one bulk
load per request resource, flat one-versus-twelve-period growth, and byte-identical serialized
dashboard responses against the former per-period path.

This is intermediate read-through batching, not the persisted trend read model. That read model
remains architectural debt; the Command Center contract and polling policy are unchanged.

| #   | Module         | Engine                                               | Key endpoints                                  |
| --- | -------------- | ---------------------------------------------------- | ---------------------------------------------- |
| 1   | Liquidity      | LCR / NSFR / stress                                  | `/banks/{id}/liquidity/*`, `/submissions/bsd3` |
| 2   | Basel Capital  | RWA / CAR-Tier1-CET1-leverage / stress               | `/banks/{id}/capital/*`, `/submissions/bsd2`   |
| 3   | Forecasting    | 5y projection / optimizer / what-if                  | `/banks/{id}/forecast/*`                       |
| 4   | Cash-flow LSTM | in-process `backend/app/ml` (LSTM + static baseline) | `/banks/{id}/cashflow-forecast`                |
| 5   | IRR (IRRBB)    | gap / duration / EVE (6 Basel) / EaR                 | `/banks/{id}/irr/*`                            |
| 6   | FX             | NOP / historical-sim VaR / IFRS 9 hedges             | `/banks/{id}/fx/*`                             |
| 7   | FTP            | matched-maturity curve / product & branch P&L / NMD  | `/banks/{id}/ftp/*`                            |

The shared migration `202607170001_irr_fx_ftp_foundation` widened the run-module, fact-group,
and line-section CHECK constraints for IRR/FX/FTP; those modules add no further migrations.

### Liquidity, stress and capital extensions

- **product.md §Phase 2 is fully built.** All 11 LMTD appendix tables; per-currency gaps +
  `usd_funding_stress` (snapshot `bank-facts-v3`); server-side EWI/CFP with the ¶74
  notification (`/banks/{id}/liquidity/ewis|cfp`); reverse stress (module
  `reverse_stress`); STRESS-PACK return (family `stress`, event-driven); IFRS 9 ECL
  (`app/domain/capital/ecl.py`; active only when `ecl_exposure` facts AND the
  `ecl-assumptions` register exist — otherwise the ingested-provisions path is
  byte-identical) + CRM haircuts (`crm_collateral` facts, Basel ¶151 code defaults +
  `crm-haircuts` register); ICAAP capital plan + quarterly ILAAP snapshots; examiner role
  (ladder position analyst > examiner > viewer — reads everything, no mutation gate admits
  it). LAS-QUARTERLY is registry+calendar REAL but generates `template_pending` until the
  official form lands (never infer a BoG layout); the monthly balance-sheet + P&L pack is
  filed as the official BSD2 and BSD7A forms. The executable completion proof is
  `tests/services/test_phase2_full_report_proof.py` — every registered return generates +
  exports (or refuses by design) over the full official-run sweep; keep it green.

### Governed forecast assumptions

- **Forecast, what-if, optimizer runs and the live forecast baseline resolve their base,
  adverse and severely adverse presets only from an approved bank-scoped
  `ForecastAssumptionVersion`** (`app/forecasting/`, the first feature package in the target
  layout). `param_stress_shock` rows with `module = 'forecast'` are no longer read; migration
  `202610070084` carried every complete register set over as approved versions.
- **Maker-checker is a runtime condition, not a screen convention.** Forecasting `edit` drafts,
  revises and submits; the decision route requires `review`, and the service requires
  `approve` with a `MAKER_CHECKER` condition that refuses whoever drafted, revised or submitted
  the version. Mutations lock the version until commit; approved and rejected versions are
  final. A bank has at most one draft or submitted version awaiting a decision.
- **Effective dating is by book date** (the run's as-of): the latest-effective approved version
  wins. Corrections may take effect before another approved version, including a future-dated
  one. New runs use the current approvals; saved inputs, hashes, results and provenance remain
  unchanged.
- **Approval enqueues the existing live and BI refresh trigger** in the approval transaction,
  using the bank's current live date.
- **Runs record `assumption_provenance` beside the snapshot** (version, effective date,
  approver), never inside the value-based `input_hash`.
- **Provisioning writes no forecast assumption values** and reports the missing approved
  base, adverse and severely adverse assumptions. The bank authors its own set; until a
  different authorized user approves it, forecasting stays not computable (`missing_parameter`).

### Known pre-existing debt (data-engine / storage tracks — not the regulatory modules)

`basedpyright` reports 8 errors in `app/services/ingestion.py`, `tests/adapters/excel_csv/
fixtures.py`, and `tests/storage/*` — all in the data-engine/storage tracks, present before
the six-module build. They are left for those tracks' owners; the regulatory modules and the
repo-wide `ruff check` are clean.
