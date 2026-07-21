# AequorOS Architecture

Single source of truth for agents building new modules. Every claim below was verified against
the code on 2026-07-14. When this document and the code disagree, the code wins — fix this file.

Companion document: [CODEBASE_CONVENTIONS.md](../CODEBASE_CONVENTIONS.md).

---

## 1. System map

| Component | Path | Stack | Role |
| --- | --- | --- | --- |
| Risk service | `backend` | FastAPI, Python 3.13, uv, SQLAlchemy 2.0, Alembic, Pydantic v2, Loguru, boto3 | The backend. Owns all persistence, calculation engines, findings, audit, and the OpenAPI contract. |
| Generated API client | `packages/risk-service-api` | typescript-fetch output of openapi-generator 7.13 | Generated from the risk-service OpenAPI schema. Source-consumed (`main: ./src/index.ts`), never hand-edited. |
| Marketing site | `frontend` | Next.js 14 | Static marketing site. **Out of scope for this build. Do not touch.** |
| Product UI | `backend/dashboard` | Next.js 14, Tailwind (token design system), TanStack Query, recharts | The Treasury Workbench — consumes the risk service exclusively through `packages/risk-service-api`. |
| Database | remote Postgres `<postgres-host>:<port>/<database>` (managed, TimescaleDB-enabled) | Primary DB for dev, tests (via `TEST_DATABASE_URL`, disposable per-run schemas), and deployment; credentials only in untracked `backend/.env` | Schema kept at alembic head. Single role, **no BYPASSRLS** — the cross-tenant worker needs a BYPASSRLS role (`WORKER_DATABASE_URL`) before running against it. |
| Local infra (offline fallback) | `backend/docker-compose.yml` | `postgres:17` on host port **15432**, MinIO on **9000** (console 9001), `risk-minio-init` creates private bucket `risk-local` | Started with `docker compose up -d` from `backend`. |

Tooling: `mise` (root `mise.toml` proxies every `risk-service:*` task into `backend/mise.toml`),
`uv` for Python deps, `pnpm` workspaces (`pnpm-workspace.yaml` includes `packages/*`, `frontend`, `backend/dashboard`). Pre-commit config is at the repo root
(`.pre-commit-config.yaml`): ruff check/format scoped to `^backend/`, Conventional
Commits enforcement, and a pre-push hook that runs `mise run risk-service:api-fresh`.

Local DB bootstrap: `mise run risk-service:bootstrap-db` creates a migration role (may bypass RLS)
and an app runtime role created with `NOBYPASSRLS`, and runs migrations. (Tenant data is never seeded — it enters through the Data Engine.)
App connection string comes from `backend/.env` (remote:
`postgresql+psycopg://<user>:<password>@<postgres-host>:<port>/<database>`; local fallback:
`postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service`).

---

## 2. Tenancy model

Verified in `backend/app/api/deps.py`, `app/db/session.py`, and migration
`alembic/versions/202605250002_enable_tenant_rls.py`.

1. **Headers → context.** Every business request carries `X-Org-Id` (required) and `X-User-Id`
   (required for mutations). `get_tenant_context` parses them into a frozen
   `TenantContext(organization_id, actor_user_id)`; invalid/missing headers → `401` before any
   service code runs. `get_mutation_tenant_context` is the same but makes `X-User-Id` mandatory.
2. **Dependency aliases** (use these, never raw `Depends(...)` in feature modules):
   - `DbSession` — tenant-validated SQLAlchemy session (`get_tenant_db_session`). It stores
     `session.info["organization_id"]` and validates that the org exists and, when present, that
     the actor is an **active user in the same org**.
   - `Tenant` — read context. `MutationTenant` — mutation context (X-User-Id required).
   - `Storage` — the `ObjectStorage` protocol (S3/MinIO), from `app/integrations/storage`.
3. **Postgres RLS as the hard safety net.** A `Session` `after_begin` event in `app/db/session.py`
   runs `SELECT set_config('app.organization_id', :org, true)` on every transaction (Postgres
   only; a no-op on SQLite). Migrations `ENABLE`/`FORCE ROW LEVEL SECURITY` on every tenant table
   and create a policy `USING (organization_id = nullif(current_setting('app.organization_id', true), '')::uuid)`.
   **Every new tenant-owned table must get the same RLS treatment in its migration.**
4. **Explicit filters are still mandatory.** Service queries always filter by
   `organization_id` (and `case_id` where applicable) even though RLS exists — for readability,
   index usage, and SQLite test compatibility.
5. **Composite FK pattern.** Child tables carry denormalized `organization_id` (and `case_id`)
   columns and declare composite `ForeignKeyConstraint`s to the parent's
   `UniqueConstraint("id", "organization_id", ...)`, so a child row can never reference a parent
   in another tenant. Exact example in
   [CODEBASE_CONVENTIONS.md](../CODEBASE_CONVENTIONS.md#composite-fk-tenant-pattern), taken from
   `app/models/calculation.py`.

---

## 3. The calculation-run pattern (reuse this for every new engine)

The reference implementation is the balance-sheet forecast + capital + liquidity chain. Concrete
tables (all in `app/models/calculation.py` and `app/models/capital.py`, migrations
`202607130002`, `202607130003`, `202607140001`):

| Table | Model | Purpose |
| --- | --- | --- |
| `calculation_runs` | `CalculationRun` | One immutable forecast attempt: status, scenario, `rerun_of_run_id`, `engine_version`, `input_schema_version`, `output_schema_version`, `input_hash` (SHA-256 of the canonical JSON snapshot), full `inputs` JSON snapshot, horizon, `as_of_date`, `started_at`/`completed_at`, `error_code`/`error_message`/`error_details`, `created_by`. |
| `calculation_forecast_periods` | `CalculationForecastPeriod` | One row per annual output period, unique on `(run_id, period_number)`, `ondelete="CASCADE"`. Money at `Numeric(20, 4)`. |
| `capital_projections` | `CapitalProjection` | Immutable capital attempt consuming one **successful** run; copies the run's `input_hash` and currency; own `engine_version` and lifecycle. |
| `capital_indicators` | `CapitalIndicator` | Per-period ratios at `Numeric(12, 8)`, `pressure_level` check constraint. |
| `capital_projection_findings` | `CapitalProjectionFinding` | Join table linking generated `RiskFinding` rows to the projection. |
| `liquidity_analysis_results` | `LiquidityAnalysisResult` | Exactly one per successful run (`UniqueConstraint("run_id")`), versioned via `analysis_version`, metrics stored as JSON. |

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
  dashboards' inline path), **upserts** one `live_metrics` row, and reconciles open `live_findings`
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
  `live_findings` across modules, surfaced by the header bell (polled).
- **Scheduler** (`app/services/scheduler.py`): the worker enqueues a `scheduled_tick`; the handler
  enqueues an `official_run` per bank whose daily filing time (`OFFICIAL_RUN_HOUR`) is due. Inert
  unless `OFFICIAL_RUN_ENABLED`, so no environment auto-mints heavy runs.
- **Robustness note.** Live compute degrades rather than fails on thin data: FX
  `compute_stressed_var` clamps the cedi-crisis window to the available return history when a bank
  has fewer observations than the configured window, so a short upload still yields a best-effort
  stress instead of killing the whole FX module. On full history the window is used unchanged.

Deferred to a later phase (foundations are laid): true CDC/streaming ingestion (only `full`
snapshot ships), WebSocket/SSE push (polling today), per-bank cron UI, email/webhook delivery.

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
4. The dashboard UI renders these findings (see `backend/dashboard` for current implementation).

---

## 5. DECISION RECORD — Legacy case vertical vs. new bank-scoped regulatory vertical

**Status: accepted for this build (2026-07). This section is a forward-looking decision, not a
description of existing tables.**

- The case-scoped credit-review vertical — `risk_cases`, documents/extractions, the financial
  workspace (`financial_*` tables), case scenarios (`risk_scenarios`, `scenario_assumptions`),
  and case-scoped `calculation_runs` / `capital_projections` / liquidity analysis — is **LEGACY**
  as of this build. It stays in place: existing features keep working, its tests keep passing,
  and its *patterns* (tenancy, immutable runs, findings, audit) are the blueprint for new work.
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
  scoping (headers → `TenantContext` → bank ownership) in
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

| Target | Commands |
| --- | --- |
| risk-service (all) | `cd backend && uv run pytest` · `uv run ruff check .` · `uv run basedpyright` — or one shot: `mise run risk-service:check` |
| risk-service vs Postgres | `docker compose up -d risk-postgres` then `mise run risk-service:test-postgres` (sets `TEST_DATABASE_URL`) |
| risk-service migrations | `mise run risk-service:migrate` (needs `DATABASE_URL`); new revision: `mise run risk-service:revision "message"` |
| web | `pnpm --filter @aequoros/dashboard typecheck` · `pnpm --filter @aequoros/dashboard lint` · `pnpm --filter @aequoros/dashboard build` · `pnpm --filter @aequoros/frontend lint` · `pnpm --filter @aequoros/frontend build` |
| generated client | `pnpm --filter @aequoros/risk-service-api test` (and `type-check`) |
| client regen + freshness | `mise run risk-service:openapi-client` then `mise run risk-service:api-fresh` (must leave git clean) |

All `mise run risk-service:*` tasks work from the repo root or from `backend`.

---

## Structure decision (2026-07 — six-module completion)

A directive proposed relocating the Python backend into `dashboard` and deleting
`frontend` / `aequoros-web`. This was **declined** as based on a misread of the layout:
the backend was already cleanly consolidated in `backend` (FastAPI) and
`backend/app/ml` (LSTM) — since flattened into a single `backend/` service with the LSTM
as the in-process `app/ml` module. Moving a Python/uv/alembic service inside a Next.js/pnpm package
would break the workspace, migrations, RLS, the OpenAPI client-gen pipeline, and the test
suite. The monorepo was kept intact; nothing was moved or deleted.

**`backend/dashboard` is the primary product surface** — the Bank Treasurer console, wired
end-to-end to the risk-service via the generated `@aequoros/risk-service-api` client, with
zero hardcoded financial data. `frontend` (marketing) remains an independent deliverable.
The legacy `aequoros-web` SPA is not present in the current repo tree (see git history).

### Six regulatory modules (all live, DB-driven, tenant-scoped)

Each follows the same pattern: pure Decimal engine in `app/domain/<module>/engine.py`,
immutable `RegulatoryRun` persistence (snapshot + SHA-256 hash + versioned metrics/line-items/
validations), bank + reporting-period scoping, effective-dated `param_*` inputs, and a
`get_<module>_dashboard` with stored-run-first + inline-fallback.

| # | Module | Engine | Key endpoints |
|---|--------|--------|---------------|
| 1 | Liquidity | LCR / NSFR / stress | `/banks/{id}/liquidity/*`, `/submissions/bsd3` |
| 2 | Basel Capital | RWA / CAR-Tier1-CET1-leverage / stress | `/banks/{id}/capital/*`, `/submissions/bsd2` |
| 3 | Forecasting | 5y projection / optimizer / what-if | `/banks/{id}/forecast/*` |
| 4 | Cash-flow LSTM | in-process `backend/app/ml` (LSTM + static baseline) | `/banks/{id}/cashflow-forecast` |
| 5 | IRR (IRRBB) | gap / duration / EVE (6 Basel) / EaR | `/banks/{id}/irr/*` |
| 6 | FX | NOP / historical-sim VaR / IFRS 9 hedges | `/banks/{id}/fx/*` |
| 7 | FTP | matched-maturity curve / product & branch P&L / NMD | `/banks/{id}/ftp/*` |

The shared migration `202607170001_irr_fx_ftp_foundation` widened the run-module, fact-group,
and line-section CHECK constraints for IRR/FX/FTP; those modules add no further migrations.

### Known pre-existing debt (data-engine / storage tracks — not the regulatory modules)

`basedpyright` reports 8 errors in `app/services/ingestion.py`, `tests/adapters/excel_csv/
fixtures.py`, and `tests/storage/*` — all in the data-engine/storage tracks, present before
the six-module build. They are left for those tracks' owners; the regulatory modules and the
repo-wide `ruff check` are clean.
