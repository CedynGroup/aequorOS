# 01. System Architecture

**Repository:** `aequorOS` · **Branch:** `eric` · **Baseline commit:** `f33e869`
**Alembic head (repository):** `202608220034` · **Prepared:** 2026-08-22

Every structural claim below is cited to a file, a migration id, or a test that fails if the
claim stops being true. Where a boundary is asserted, the guard that enforces it is named.

---

## 1. Deployable components

| Component | Path | Entrypoint | Port | Role |
|---|---|---|---|---|
| Tenant API | `backend/app` | `app.main:app` | 8000 | All bank-facing endpoints, engines, persistence |
| Background worker | `backend/app` | `python -m app.worker` | — | Cross-tenant job queue: ingestion refresh, official runs, market-data pulls, scheduled ticks |
| Operator control plane | `backend/app/operator` | `uvicorn app.operator.main:app` | 8100 | Staff-only cross-tenant administration |
| Product UI | `backend/dashboard` | Next.js 14 | — | Treasury workbench; consumes the API through the generated client only |
| Operator console | `console` | Next.js 14 | — | Staff UI; all traffic via its own `/api/op` proxy |
| Generated API client | `packages/risk-service-api` | typescript-fetch | — | Generated from `backend/openapi-schema.json`; never hand-edited |
| Marketing site | `frontend` | Next.js 14 | — | Static; out of the regulatory scope |

Deployment topology is `backend/docker-compose.prod.yml`: `risk-migrate` (runs
`alembic upgrade head` and exits), `risk-api`, `risk-worker`, `risk-operator`. Each of the
three long-running services carries a healthcheck; `risk-api` probes
`/api/health/ready` (`docker-compose.prod.yml:54`), `risk-worker` probes
`app.worker:healthcheck` (`:84`), `risk-operator` probes `/operator/health` (`:108`).

> **Path note.** The product UI lives at `backend/dashboard`, not repo-root `dashboard/`
> (`pnpm-workspace.yaml` lists `backend/dashboard`). Source audits cite it as
> `dashboard/…`; those paths resolve under `backend/`.

### The operator plane is a separate application

`app/operator/` is the third ASGI entrypoint and is **never** mounted on the tenant API. It
runs with a cross-tenant `BYPASSRLS` session by design. Route isolation is pinned by test,
not by convention — see §08.

---

## 2. The calculation planes, and why there is more than one

The forensic architecture audit's headline finding was that no single calculation pipeline
exists. That finding stands, and it is architecture rather than defect — but only because
each plane's authority is now declared and the boundaries between them are machine-checked.

| Plane | Storage | Authority | Reaches a filing? |
|---|---|---|---|
| Bank regulatory / treasury | `banks`, `bank_reporting_periods`, `bank_financial_facts`, `regulatory_runs` | Authoritative for universal-bank Basel/BoG metrics | Yes |
| SDI regime (Act 930 s.29) | canonical positions / references + `regulatory_parameter` | Authoritative for s.29 metrics; **not** Basel | Return pack not published by BoG — see §15 |
| Case financial workspace (legacy) | `risk_cases`, `financial_*`, `calculation_runs`, `capital_projections` | Case analysis only | **No** — structurally forbidden |
| Regulatory reporting templates | `regulatory_packages`, committed BoG layouts + line maps | Template-authoritative: the workbook's own formulas | Yes |
| Scenario workbench | bank facts + params, no `RegulatoryRun` | Advisory / transient | No |

Source: `backend/docs/FORENSIC_CALCULATION_ARCHITECTURE_AUDIT_2026-08-21.md` §2, verified
against `app/domain/`, `app/services/regulatory_*.py` and `app/models/`.

### The case plane cannot become an alternate regulatory authority (`CF-2`, `CF-4`)

The separation is enforced by an AST-based closure rule over the whole of `app/`, not by a
hand-kept list of forbidden files:

- `tests/architecture/test_case_plane_boundary.py` — only the modules named in
  `CASE_PLANE_OWNERS` may reference a case-plane model
  (`test_only_the_case_plane_itself_references_a_case_plane_model`), the owner list must
  name modules that exist (`test_the_case_plane_owner_list_is_exact`), the reverse
  direction is guarded too (`test_the_case_plane_never_reads_bank_facts_or_sealed_runs`),
  and no module may write case output into a bank fact
  (`test_no_module_can_write_case_output_into_a_bank_fact`).
- Both scans are proved non-vacuous
  (`test_the_regulatory_plane_scan_is_not_vacuous`, `test_the_case_plane_scan_is_not_vacuous`)
  and both guards are proved to convict a planted violation
  (`test_the_forward_guard_convicts_a_planted_import`,
  `test_the_forward_guard_convicts_an_aliased_module_import`,
  `test_the_reverse_guard_convicts_a_planted_import`).
- Naming a forbidden symbol inside a string literal is correctly *not* a dependency
  (`test_naming_a_forbidden_symbol_in_a_string_is_not_a_dependency`) — this matters
  because `app/domain/authority/registry.py` names the case plane in order to forbid it.

`tests/architecture/test_dependency_boundaries.py` adds three further guards (`ARCH-5`):
regulatory reporting never reads a case `CalculationRun`; the official bank run never reads
case financial records; the pure domain layer imports no application state — plus
`test_the_boundary_scanner_catches_a_deliberate_violation`.

---

## 3. The two computation tiers

One canonical store, two tiers (`ARCHITECTURE.md` §3b, verified in `app/services/pipeline.py`,
`app/services/job_queue.py`, `app/worker.py`):

| Tier | Job type | Writes | Purpose |
|---|---|---|---|
| Live | `pipeline_refresh` | `live_metrics`, `live_findings`, `current_financial_facts` | Intraday awareness; creates **zero** `RegulatoryRun` rows |
| Official | `official_run` | immutable `RegulatoryRun` + line items + validations | The filing plane |

Ingestion is event-driven: an accepted upload or API push enqueues a debounced
`pipeline_refresh` (coalesced on a `coalesce_key`). Official runs are minted on schedule
(`OFFICIAL_RUN_ENABLED`, default off) or on demand.

### Worker visibility is now a health signal, not an assumption (`P0-16`)

`jobs` is FORCE-RLS, and the worker claims across tenants with no organization set — so a
role without `BYPASSRLS` matched zero rows forever, with no error and no log. Now:

- `assert_worker_database_access` guards both the process worker and
  `start_inprocess_worker` **before the thread starts** (an exception on a daemon thread is
  as silent as the bug it replaces).
- Production and staging require `WORKER_DATABASE_URL` (`app/core/config.py` validators).
- `worker_visibility()` surfaces a `worker` component in `/health/ready`
  (`app/api/health.py:_worker_health`), including a starvation signal derived from
  `_overdue_job_count`.
- Migration `202608220030` persists `worker_heartbeats`; `GET /operator/v1/worker-health`
  classifies staleness against `WORKER_HEARTBEAT_STALE_SECONDS`.
- Evidence: `tests/test_worker.py`, `tests/test_health.py`.

---

## 4. Readiness contract

`ReadinessResponse` was reshaped from `database:{status,storage}` to
`checks:{database,storage,worker,signing}` with a typed `ComponentHealth`
(`app/schemas/health.py`). Storage is no longer reported as a property of the database.

Storage readiness now calls `StorageClient.health_check()` rather than testing whether the
S3 environment variables are non-empty (`app/api/health.py::_storage_health`, `P0-17`).
Healthy, unhealthy and exception paths are covered by `tests/test_health.py`.

---

## 5. Purity of the domain layer

`app/domain/**` is pure Decimal computation. `app/domain/policy/resolver.py` (606 lines)
imports no SQLAlchemy, no FastAPI and nothing from `app.services`;
`app/services/regulatory_parameters.py` is a thin database adapter over it (`ARCH-2`).
The purity rule is enforced by
`tests/architecture/test_dependency_boundaries.py::test_the_pure_domain_layer_imports_no_application_state`.

Shared primitives, all in `app/domain/`:

| Primitive | Module | Lines |
|---|---|---|
| Metric authority registry (`ARCH-1`) | `app/domain/authority/registry.py` | 1,785 |
| Fail-closed outcome states (`ARCH-3`) | `app/domain/authority/outcomes.py` | 386 |
| Calculation provenance (`ARCH-4`) | `app/domain/authority/provenance.py` | 282 |
| Policy resolver (`ARCH-2`) | `app/domain/policy/resolver.py` | 606 |

`OutcomeState` (`outcomes.py:82-93`) declares exactly five refusal states —
`not_computable`, `missing_required_input`, `policy_unresolved`, `data_quality_block`,
`reconciliation_failed` — with a `Severity` of `blocking` or `advisory`. Every engine that
cannot compute returns one of these rather than a substituted value.

---

## 6. Contract flow

Backend routes/schemas change → `mise run risk-service:openapi-client` regenerates
`backend/openapi-schema.json` and `packages/risk-service-api` → `mise run risk-service:api-fresh`
asserts the working tree is clean for both. The freshness gate runs pre-push
(`.pre-commit-config.yaml`) and as the `api-fresh` CI job
(`.github/workflows/risk-service.yml:211`).

---

## 7. Migration chain

96 revisions under `backend/alembic/versions/`. The chain from `202608210026` to the head is
linear and single-headed (verified by reading `revision`/`down_revision` on every
`2026082200*` file):

```
202608210026 → 27 → 28 → 29 → 30 → 31 → 32 → 33 → 202608220034
```

| Revision | Subject |
|---|---|
| `202608220027` | Row-level security on `current_financial_facts` (`P0-1`) |
| `202608220028` | Revocable, rotating refresh tokens (`P0-5`) |
| `202608220029` | Re-runs and verifies the historical BoG return-code recode (`P0-18`) |
| `202608220030` | `worker_heartbeats` liveness evidence (`P0-16`) |
| `202608220031` | Append-only triggers on approval and submission evidence (`AUD-1`) |
| `202608220032` | Reconciliation control tables and seed (`P0-10`) |
| `202608220033` | Temenos connection reporting currency required (jurisdiction defaults) |
| `202608220034` | Basel HQLA haircuts and Level-2 caps in the control plane (`P0-8`) |

**A transient duplicate revision id at `202608220031` occurred mid-programme and was
resolved by re-chaining; the chain is single-headed today.**


> ### ⚠ SUPERSEDED 2026-08-22 (close of day) — the production migration gap is CLOSED
>
> Re-probed read-only at the close of 2026-08-22 (libpq
> `options=-c default_transaction_read_only=on`, probe write refused first): the production
> primary is at **`202608230039`**, **level with the repository head**. Every migration named
> in this section has been applied. Verified by **effect**, not by the version string:
> `implied_rating_runs` and `market_data_entitlements` both `rowsecurity=True forced=True
> policies=1` with a fail-closed predicate; `reconciliation_exceptions_governed_row` and
> `regulatory_parameter_governed_row` both present; the run parameter-provenance column
> present; the `organization_id` RLS gap is exactly the three documented
> `CROSS_TENANT_BY_DESIGN` tables (120 of 123 forced with a policy, 0 undocumented).
>
> The text below is the earlier measurement and is retained deliberately — the sequence is
> the evidence, and `INF-3` is a **standing gate that re-opens on the next migration**, not an
> event that was discharged. No automated check yet fails when the primary falls behind the
> repository head. Full detail: `remediation_master_register.md` §WS-A12-R2 §R2-13.

> **Production gap — measured, not inferred (EARLIER on 2026-08-22, now superseded).** A
> read-only query of the production primary returned `alembic_version = 202608210026`. **All
> eight revisions above were unapplied in production** at that moment. The consequences were
> itemised in §14 and §15.

---

## 8. Deployment sharp edges recorded in the codebase

These are operational constraints an acquirer inherits; each is documented in `CLAUDE.md`
with the incident that established it.

- Compose files deployed through Coolify must not use `${...}` interpolation (an incident
  on 2026-07-21 corrupted the backend's environment store); services load `env_file: .env`
  and fail-fast lives in the application's settings validators.
- Coolify materialises only the compose file on the host — a bind mount of a repository
  file has no source and Docker creates an empty directory in its place. Configuration
  belongs inside the compose file.
- Pre-creating tables on the shared primary ahead of the migration chain makes
  `risk-migrate` exit 1 on redeploy. The primary must be reconciled to head at deploy.
- Alembic runs as the application role: data steps on FORCE-RLS tables silently no-op
  unless run under a `BYPASSRLS` role or inside
  `app/db/session.py::force_rls_suspended` (`P0-18`).
