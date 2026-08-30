# AequorOS Architecture

Single source of truth for agents building new modules. Initially verified on
2026-07-14 and updated through 2026-08-29. When this document and the code
disagree, the code wins — fix this file.

Companion document: [CODEBASE_CONVENTIONS.md](CODEBASE_CONVENTIONS.md).

---

## 1. System map

| Component                      | Path                                                                               | Stack                                                                                                                                         | Role                                                                                                                                                           |
| ------------------------------ | ---------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Risk service                   | `backend`                                                                          | FastAPI, Python 3.13, uv, SQLAlchemy 2.0, Alembic, Pydantic v2, Loguru, boto3                                                                 | The backend. Owns all persistence, calculation engines, findings, audit, and the OpenAPI contract.                                                             |
| Generated API client           | `packages/risk-service-api`                                                        | typescript-fetch output of openapi-generator 7.13                                                                                             | Generated from the risk-service OpenAPI schema. Source-consumed (`main: ./src/index.ts`), never hand-edited.                                                   |
| Marketing site                 | `frontend`                                                                         | Next.js 14                                                                                                                                    | Static marketing site. **Out of scope for this build. Do not touch.**                                                                                          |
| Product UI                     | `dashboard`                                                                        | Next.js 14, Tailwind (token design system), TanStack Query, recharts                                                                          | The Treasury Workbench — consumes the risk service exclusively through `packages/risk-service-api`.                                                            |
| Database                       | remote Postgres `<postgres-host>:<port>/<database>` (managed, TimescaleDB-enabled) | Primary DB for dev, tests (via `TEST_DATABASE_URL`, disposable per-run schemas), and deployment; credentials only in untracked `backend/.env` | Schema kept at alembic head. Single role, **no BYPASSRLS** — the cross-tenant worker needs a BYPASSRLS role (`WORKER_DATABASE_URL`) before running against it. |
| Local infra (offline fallback) | `backend/docker-compose.yml`                                                       | `postgres:17` on host port **15432**, MinIO on **9000** (console 9001), `risk-minio-init` creates private bucket `risk-local`                 | Started with `docker compose up -d` from `backend`.                                                                                                            |

Tooling: `mise` (root `mise.toml` proxies every `risk-service:*` task into `backend/mise.toml`),
`uv` for Python deps, `pnpm` workspaces (`pnpm-workspace.yaml` includes `packages/*`, `frontend`, `dashboard`). Pre-commit config is at the repo root
(`.pre-commit-config.yaml`): ruff check/format scoped to `^backend/`, Conventional
Commits enforcement, and a pre-push hook that runs `mise run risk-service:api-fresh`.

Local DB bootstrap: `mise run risk-service:bootstrap-db` creates a migration role (may bypass RLS)
and an app runtime role created with `NOBYPASSRLS`, runs migrations, and seeds two demo tenants.
App connection string comes from `backend/.env` (remote:
`postgresql+psycopg://<user>:<password>@<postgres-host>:<port>/<database>`; local fallback:
`postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service`).

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

Liquidity Monitoring is the first enforcing product route: it requires `view`
on an exact institution target (or an explicitly organization-wide binding)
for LIQ/confidential and emits `authz.binding_decision`. It denies when no
complete active binding matches or evaluation fails; scalar roles are not a
fallback. Other product routes retain their existing authorization behavior.
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
at a time and aggregate Settings → Members. Create and revoke are audited,
assignment SoD is authoritative, and each mutation invalidates the grantee's
sessions transactionally. SSO request approval uses the same complete-grant
path. This administration boundary is enforcing; ordinary product routes are
still on the legacy gates described above.

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
  `RegulatoryRun` rows. Forecast has no cheap path, so its live row mirrors the latest succeeded
  official forecast run (populated on the next refresh after an official run).
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
  heavy runs.
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

| Target                   | Commands                                                                                                                                                                                                                                  |
| ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| risk-service (all)       | `cd backend && uv run pytest` · `uv run ruff check .` · `uv run basedpyright` — or one shot: `mise run risk-service:check`                                                                                                                |
| risk-service vs Postgres | `docker compose up -d risk-postgres` then `mise run risk-service:test-postgres` (sets `TEST_DATABASE_URL`)                                                                                                                                |
| risk-service migrations  | `mise run risk-service:migrate` (needs `DATABASE_URL`); new revision: `mise run risk-service:revision "message"`                                                                                                                          |
| dashboard                | `pnpm --filter @aequoros/dashboard typecheck` · `lint` · `test` · `build` · `e2e` (production build includes the Command Center Recharts deferral guard; package-capable Playwright specs need S3/MinIO, while storage-free specs do not) |
| marketing                | `pnpm --filter @aequoros/frontend lint` · `build`                                                                                                                                                                                         |
| operator console         | `pnpm --filter @aequoros/console typecheck` · `test` · `build` (no ESLint: the workspace has no ESLint dependency or config)                                                                                                              |
| generated client         | `pnpm --filter @aequoros/risk-service-api test` (and `type-check`)                                                                                                                                                                        |
| client regen + freshness | `mise run risk-service:openapi-client` then `mise run risk-service:api-fresh` (must leave git clean)                                                                                                                                      |

All `mise run risk-service:*` tasks work from the repo root or from `backend`.

**Every row above is enforced in CI** (2026-08-22 — before that, `frontend/` and `console/`
appeared in no workflow at all, and the dashboard workflow ran neither `lint` nor `test`, so
the regulatory fail-open guard and the browser-runtime SSRF guard were unenforced):

| Workflow                             | Jobs                                                                                                                                                                                                                     |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `.github/workflows/risk-service.yml` | `static` · `architecture` · `unit` · `postgres` · `postgres-suite` · `storage` · `journeys` (Playwright + MinIO; exact eight-journey fixture-drift quarantine tracked in #151) · `api-fresh` · `real-data` (conditional) |
| `.github/workflows/dashboard.yml`    | `dashboard` — client typecheck, dashboard typecheck, lint, test, production build + Command Center entry-graph guard                                                                                                     |
| `.github/workflows/web.yml`          | `frontend` — lint, build · `console` — typecheck, test, build                                                                                                                                                            |

Each workflow carries its own gate inventory in a header comment; keep it accurate when you
add a step.

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

### Known pre-existing debt (data-engine / storage tracks — not the regulatory modules)

`basedpyright` reports 8 errors in `app/services/ingestion.py`, `tests/adapters/excel_csv/
fixtures.py`, and `tests/storage/*` — all in the data-engine/storage tracks, present before
the six-module build. They are left for those tracks' owners; the regulatory modules and the
repo-wide `ruff check` are clean.
