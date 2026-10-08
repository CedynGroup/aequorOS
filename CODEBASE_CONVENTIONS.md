# AequorOS Codebase Conventions

Verified against the code on 2026-07-14. Companion to [ARCHITECTURE.md](ARCHITECTURE.md).
Match existing code exactly; do not introduce new patterns when one below already fits.

---

## 1. Python (backend)

Paths below describe the existing layered tree; new feature code follows
[the target feature layout](#5-feature-layout).

### Tooling (from `pyproject.toml`)

- **ruff**: `line-length = 100`, `target-version = "py313"`. Lint rule set:
  `select = ["E", "F", "I", "UP", "B", "SIM", "PL"]`, `ignore = ["PLR2004"]`. When a function
  legitimately needs many parameters, the existing code suppresses per-line with
  `# noqa: PLR0913` (and `PLR0915` for long orchestration functions) rather than restructuring.
- **basedpyright**: `typeCheckingMode = "strict"`, `pythonVersion = "3.13"`, with included paths
  defined in `backend/pyproject.toml`; `reportAny` and the `reportUnknown*` family are errors, and
  `reportExplicitAny` is off so the JSON-column type below stands. The
  [typing ratchet](#typing-ratchet) enforces it.
- Every module starts with `from __future__ import annotations`.
- Python 3.13 syntax is used freely: `type X = Literal[...]` aliases, `X | None`, `StrEnum`.

### Typing ratchet

New code is strict; legacy code may only get stricter.

- `mise run risk-service:typecheck` runs `backend/scripts/typing_ratchet.py check`. It type-checks
  the backend once and counts each module's errors and warnings per rule against
  `backend/scripts/typing_baseline.json`.
- **Strict modules** may have no errors and never take a baseline entry: every new module, every
  module that passed when the baseline was recorded, and the packages and modules listed in
  `STRICT_MODULES` in the script.
- **A legacy module may not add errors.** A rule's count above its baseline fails.
- **Record every fix.** A count below its baseline also fails until
  `uv run python scripts/typing_ratchet.py update`, run from `backend/`, writes the lower count.
  `update` refuses to run while any count is above its baseline, so it only lowers numbers and
  deletes entries; review that the diff does only that.
- **Moves.** A module moved by a layout PR keeps its legacy allowance until its package is
  promoted, so a move PR stays a pure rename (§5). The ratchet counts it under the name it had
  before its moves in `backend/scripts/feature_module_moves.json`. A module whose new location is
  already in `STRICT_MODULES` gets no allowance: fix its errors in the move PR, and `update` then
  removes its old entry.
- **Promoting a package.** A package joins `STRICT_MODULES` once it is clean: fix its modules'
  errors, run `update` so the baseline holds no entry for them (moved modules' entries sit under
  their pre-move names), then add the package to `STRICT_MODULES`.
  `tests/architecture/test_typing_ratchet.py` then rejects any baseline entry for it.
- Plain `uv run basedpyright <files>` shows a file's strict errors, legacy ones included, and is
  not a gate. Narrow `Any` with `isinstance`, a pydantic `TypeAdapter` or a typed SQLAlchemy result
  (`.tuples()`, `.scalars()`) rather than silencing it.

### SQLAlchemy models (`app/models/*.py`)

- SQLAlchemy 2.0 declarative style only: `Mapped[...]` + `mapped_column(...)`. No legacy
  `Column =` assignments, no `relationship()` (the codebase queries explicitly instead).
- Annotate `__table_args__: TableArgs` (from `app/db/base.py`); `DeclarativeBase` types it as
  `Any`, which strict mode rejects.
- Base and mixins from `app/db/base.py`:
  - `UuidV4PrimaryKeyMixin` — default for workflow tables (cases, runs, findings, capital).
  - `UuidV7PrimaryKeyMixin` — used by the `financial_*` canonical tables (time-ordered ids).
  - `TimestampMixin` — `created_at`/`updated_at`, timezone-aware, `utc_now` default +
    `onupdate`. Append-only tables (history, evidence, audit) skip the mixin and declare only
    `created_at` with `default=utc_now`.
- Enum-like strings are **not** DB enums: `Mapped[str]` + `CheckConstraint`, e.g. from
  `app/models/calculation.py`:
  ```python
  CheckConstraint(
      "status IN ('queued', 'running', 'succeeded', 'failed')",
      name="ck_calculation_runs_status",
  )
  ```
  Constraint names: `ck_<table>_<field>`; unique: `uq_<table>_<cols>`; index: `ix_<table>_<cols>`.
  The Python-side allow-lists/StrEnums live in `app/domain/risk_constants.py`.
- **Numeric precision**: money `Numeric(20, 4)`; ratios `Numeric(12, 8)`; interest rates
  `Numeric(10, 6)`; covenant thresholds/actuals `Numeric(20, 6)`; confidence `Numeric(5, 4)`.
- **Decimal math**: always `decimal.Decimal` with `ROUND_HALF_UP`, quantized through module
  constants before persistence/classification:
  `MONEY = Decimal("0.0001")` (4 dp, both engines); capital `RATIO = Decimal("0.00000001")`
  (8 dp); liquidity display ratios quantize to `Decimal("0.0001")` (4 dp). Calculations guard
  overflow against `MAX_STORED_MONEY = Decimal("9999999999999999.9999")`. Never use float for
  financial values.
- **JSON columns** for snapshots/details/diagnostics: `Mapped[dict[str, Any]] = mapped_column(
JSON, default=dict, server_default=sql_text("'{}'"), nullable=False)` (lists use
  `default=list, server_default=sql_text("'[]'")`). Models declare generic `JSON`; migrations
  declare `postgresql.JSONB`. A column named `metadata` maps as `metadata_: Mapped[...] =
mapped_column("metadata", JSON, ...)` because `metadata` is reserved on the Base.

### Composite-FK tenant pattern

Exact pattern from `app/models/calculation.py` — every tenant-owned child denormalizes
`organization_id` (and `case_id`) and references the parent through a composite FK against the
parent's `(id, organization_id, ...)` unique constraint:

```python
class CalculationRun(UuidV4PrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "calculation_runs"
    __table_args__ = (
        ...
        ForeignKeyConstraint(
            ["case_id", "organization_id"],
            ["risk_cases.id", "risk_cases.organization_id"],
        ),
        ForeignKeyConstraint(
            ["scenario_id", "organization_id", "case_id"],
            ["risk_scenarios.id", "risk_scenarios.organization_id", "risk_scenarios.case_id"],
        ),
        UniqueConstraint(
            "id", "organization_id", "case_id", name="uq_calculation_runs_id_org_case"
        ),
    )

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
```

The `UniqueConstraint("id", "organization_id", ...)` on the parent is what lets children FK to
the composite key. Child output tables add `ondelete="CASCADE"` on the composite FK. New
bank-scoped tables follow the same pattern with `bank_id` in place of `case_id`.

### Migrations (`alembic/versions/`)

- Filename and revision id: `YYYYMMDDNNNN_short_description.py` (e.g.
  `202607140001_liquidity_analysis_results.py`), `revision = "202607140001"`,
  `down_revision = "<previous>"`. `NNNN` is a same-day sequence starting at `0001`.
- Style (see `202607130003_capital_projection.py` and `202607140001_...`): hand-written explicit
  `op.create_table`/`op.create_index`/`op.execute` calls; **no autogenerate artifacts or
  commented placeholders**. `mise run risk-service:revision "msg"` uses `--autogenerate` as a
  starting point, but the committed file must be cleaned to this style.
- Dialect types in migrations: `postgresql.UUID(as_uuid=True)`,
  `postgresql.JSONB(astext_type=sa.Text())` with `server_default=sa.text("'{}'::jsonb")`.
- Repeated strings become module-level constants
  (`TENANT_ID_EXPR = "nullif(current_setting('app.organization_id', true), '')::uuid"`,
  `TABLE = "..."`).
- Every new tenant table enables RLS in the same migration:
  ```python
  op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
  op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
  op.execute(f"""
      CREATE POLICY {TABLE}_tenant_isolation ON {TABLE}
      USING (organization_id = {TENANT_ID_EXPR})
      WITH CHECK (organization_id = {TENANT_ID_EXPR})
      """)
  ```
- `downgrade()` fully reverses (drop policy → disable RLS → drop indexes → drop table).

### API layer (`app/features/`)

- One module per use case, named `app/features/<verb_noun>.py` (`run_calculations.py`,
  `manage_capital.py`, `review_liquidity.py`, `read_financial_workspace.py`, ...). Each exposes
  `router = APIRouter(tags=["<domain>"])` and is registered in `app/api/router.py` under the
  `v1_router` (`/api/v1`). Health stays outside versioning at `/api/health`.
- Route handlers are thin: parse params, delegate to a service function, return the schema.
  No SQL, no model mutation in feature modules.
- `response_model=` is always set; creation routes add `status_code=status.HTTP_201_CREATED`.
- **Operation ids are camelCase.** `app/main.py::generate_operation_id` derives them from the
  route function name (`get_liquidity_summary` → `getLiquiditySummary`); routes may also set
  `operation_id="..."` explicitly (see `review_liquidity.py`). Either way the generated TS client
  gets camelCase method names — keep route function names snake_case and descriptive.
- Query params use `Annotated[..., Query(...)]` with validation, e.g.
  `limit: Annotated[int, Query(ge=1, le=100)] = 25`, `offset: Annotated[int, Query(ge=0)] = 0`.
- Dependencies come only from `app/api/deps.py`: `DbSession`, `Tenant`, `MutationTenant`,
  `Storage`.

### Schemas (`app/schemas/<domain>.py`)

- Pydantic v2 models, one module per domain. Each module defines a local
  `class ClosedModel(BaseModel): model_config = ConfigDict(extra="forbid")` base; request and
  response models inherit from it so unknown fields are rejected.
- Suffix conventions: `<Thing>Create` / `<Thing>Update` for request bodies, `<Thing>Read` for
  responses, `<Thing>ListRead` for paginated lists (fields: `total`, `limit`, `offset`,
  `has_more` + collection), `<Thing>SummaryRead` for trimmed list rows.
- Literal `type` aliases for enums (`type CalculationStatus = Literal["queued", ...]`).
- Cross-field rules via `@model_validator(mode="after")` (see
  `LiquidityFindingReview.require_dismissal_reason`).
- ORM-loaded rows use `model_config = ConfigDict(from_attributes=True)` (see
  `ForecastPeriodRead`). `Field(title=...)` is used to disambiguate duplicate OpenAPI titles.

### Services (`app/services/<domain>.py`)

- Own use-case orchestration, transaction boundaries, tenant-scoped queries, audit events.
  Signature convention: `def fn(db: Session, ctx: TenantContext, case_id: UUID, payload, ...)`.
- Errors are `fastapi.HTTPException` with `status.HTTP_*` constants and short human messages
  ending in a period. Cross-tenant/missing = `404`; state conflicts (archived, wrong status,
  read-only finding) = `409`; missing actor = `401`; invalid values = `400`.
- Existence helpers follow `get_case_or_404(db, organization_id, id)` /
  `get_case_for_update_or_404` (adds `.with_for_update()`) / `ensure_case_is_not_archived` from
  `app/services/cases.py`. Write the same trio for new aggregates (e.g. `get_bank_or_404`).
- Audit: `from app.services.audit import record_event` — call it in the same transaction as the
  change with dotted `event_type` (`"capital_projection.started"`,
  `"liquidity_finding.reviewed"`), `entity_type`, `entity_id`, and a JSON-safe `details` dict
  (UUIDs stringified).
- Module-level constants for versions and rule ids:
  `ENGINE_VERSION = "capital-projection-v1.0.0"`, `RULE_VERSION = "liquidity-v1.0.0"`,
  `NEGATIVE_CASH_RULE_ID = "liquidity.negative_cash"`, thresholds as `Decimal` constants.
- Domain input failures are typed exceptions carrying a payload
  (`CalculationInputError`, `CapitalInputError` with `{code, message, details}`) that services
  convert into persisted `failed` rows — not HTTP 500s.
- Pure calculation logic (no db/ctx) lives in plain functions like
  `liquidity.calculate_metrics(periods)` so it is unit-testable; its target home follows
  [the feature layout](#5-feature-layout).
- Storage access only through the `ObjectStorage` protocol
  (`app/integrations/storage/base.py`); `S3ObjectStorage` + `get_object_storage()` in
  `s3.py` is the sole boto3 call site. Never import boto3 in features/services.

### Tests (`backend/tests/`)

- Layout and fixture ownership follow [the feature layout](#5-feature-layout).
  HTTP-level tests are the default style.
- **Database fixtures**: see [CONTRIBUTING.md](CONTRIBUTING.md) for fixture selection and
  the [backend test database guide](backend/README.md#test-databases-and-the-primary-database)
  for isolation and application reuse.
  Use `mise run risk-service:test-postgres` to exercise the same suite against Postgres;
  Postgres-only behavior (RLS, advisory locks) is written to no-op on SQLite.
- **Fixtures** (conftest): `client` (no DB), `db_client`, `db_session`, `api_factories`,
  `fake_storage`, `tenant_ctx`, `test_settings`/`db_settings`.
- **Tenant constants** from `tests/support/helpers.py`: `ORG_1`, `ORG_2`, `USER_1`, `USER_2`, and
  `headers(org_id, user_id, roles, authorization_version)`, which returns a signed
  `Authorization: Bearer ...` access token. It defaults to the seeded user's current
  authorization version (`1`); stale-session tests pass an older value explicitly.
- **Factories**: `tests/support/api_factories/` package — `ApiFactories` bundles `CaseFactory`,
  `DocumentFactory`, `AssessmentFactory` (+ `MutableFakeStorage`); factories create data through
  the real HTTP API and assert status codes.
- **Cross-tenant isolation test pattern** — every new endpoint needs one. Canonical example:
  `tests/api/test_liquidity.py::test_liquidity_summary_and_review_are_tenant_scoped` — create
  data as ORG_1, then assert the same URLs return `404` with
  `headers(org_id=ORG_2, user_id=USER_2)` for both reads and mutations. DB-level scoping checks
  live in `tests/api/test_scoping.py`.
- OpenAPI contract regression: `tests/api/test_openapi_contract.py`.

---

## 2. Authenticated bank dashboard (`backend/dashboard`)

The case-based `apps/aequoros-web` SPA was removed. The authenticated bank
product is the Next.js App Router package at `backend/dashboard`; its detailed
screen, regulatory-copy, arithmetic, and local-development rules live in
[`backend/dashboard/README.md`](backend/dashboard/README.md).

### API access

- Use the generated `@aequoros/risk-service-api` classes and types. Never
  duplicate OpenAPI payloads or hand-roll tenant identity headers.
- Import the shared `configuration` from `backend/dashboard/lib/api/client.ts`.
  It sets the generated client's `basePath` and supplies a current app access
  token through `Configuration.accessToken`; generated requests therefore send
  `Authorization: Bearer <token>`.
- The verified bearer token establishes organization, actor, legacy role, and
  authorization version. Browser-supplied `X-Org-Id` / `X-User-Id` values are
  not an identity mechanism and must not be added to new clients.
- `TokenSync` keeps the browser token cache current. The access-token callback
  falls back to `getSession()` before the first sync and supports the separate
  read-only operator-impersonation bearer lifecycle.
- Direct `fetch` calls are exceptional (downloads, server-only attestation
  routes, and non-generated endpoints); they must attach the same bearer token
  and must not recreate generated request/response shapes.

### Query and validation conventions

- TanStack Query hooks are centralized under `backend/dashboard/lib/api/`; keep
  query keys resource-specific and invalidate the affected resource after a
  successful mutation.
- Missing backend values remain missing. Follow
  `backend/dashboard/lib/api/values.ts` and the fail-open guard: never turn an
  absent denominator into zero or write a regulatory threshold into UI code.
- Validate with `pnpm --filter @aequoros/dashboard typecheck`, `lint`, `test`,
  and `build`; run the package's Playwright suite for end-to-end changes.

---

## 3. Reusable inventory

### Backend

| Helper                                                                                                                                                                                 | Where                                                        | Use for                                                                                                |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------ |
| `DbSession`, `Tenant`, `MutationTenant`, `Storage`                                                                                                                                     | `app/api/deps.py`                                            | Every route's session/tenant/storage wiring; context type location: [§5](#5-feature-layout).           |
| `record_event(db, ctx, *, event_type, entity_type, entity_id, details)`                                                                                                                | `app/services/audit.py`                                      | Audit trail for every meaningful mutation, same transaction.                                           |
| `get_case_or_404` / `get_case_for_update_or_404` / `ensure_case_is_not_archived` / `ensure_status_transition_allowed`                                                                  | `app/services/cases.py`                                      | Tenant-scoped existence + state guards (template for `get_bank_or_404`).                               |
| `get_finding_or_404`, `list_findings`, `list_case_findings`, `create_case_finding`, `update_finding`, `apply_finding_update`, `is_liquidity_workflow_finding`, `list_finding_evidence` | `app/services/findings.py`                                   | Generic finding CRUD/review; reuse for new engines' findings.                                          |
| `calculate_metrics`, `generate_findings`, `lock_finding_publication`, `serialize_finding_publication`                                                                                  | `app/services/liquidity.py`                                  | Template for deterministic metric + finding publication with advisory-lock serialization.              |
| `MONEY`, `RATIO`, `MAX_STORED_MONEY`, `_money`/`_ratio` quantizers, `_snapshot_hash`                                                                                                   | `app/services/calculations.py`, `capital.py`, `liquidity.py` | Money/ratio rounding constants and SHA-256 input hashing (copy the constants; keep values consistent). |
| `RISK_TYPES`, `FindingStatus`, `Severity`, `CaseStatus`, `FindingSource`, derived sets                                                                                                 | `app/domain/risk_constants.py`                               | Shared enum values; extend here, not inline.                                                           |
| `ObjectStorage` protocol, `get_object_storage`                                                                                                                                         | `app/integrations/storage/`                                  | All object-storage access; override in tests via dependency_overrides.                                 |
| `Base`, `UuidV4PrimaryKeyMixin`, `UuidV7PrimaryKeyMixin`, `TimestampMixin`, `utc_now`                                                                                                  | `app/db/base.py`                                             | Model building blocks.                                                                                 |
| `Settings` / `get_settings()`                                                                                                                                                          | `app/core/config.py`                                         | Env config; add nested `BaseSettings` groups with env aliases.                                         |
| `ORG_1/ORG_2/USER_1/USER_2`, `headers()`, `ApiFactories`, `FakeStorage`, `db_client`                                                                                                   | `tests/`                                                     | Test tenancy, data factories, storage stubbing.                                                        |

### Frontend

| Helper                                           | Where                                 | Use for                                                        |
| ------------------------------------------------ | ------------------------------------- | -------------------------------------------------------------- |
| `configuration`, `apiBaseUrl`, `apiOrigin`       | `backend/dashboard/lib/api/client.ts` | Generated-client setup and the single API-origin authority.    |
| `setAccessToken`, `getAccessToken`               | `backend/dashboard/lib/api/token.ts`  | Expiry-aware bearer-token cache synchronized with NextAuth.    |
| Generated `*Api` classes and wire types          | `packages/risk-service-api`           | Every supported tenant API request and response.               |
| TanStack Query hooks                             | `backend/dashboard/lib/api/hooks.ts`  | Shared server-state reads, mutations, keys, and invalidation.  |
| `numOrNull`, `assessAgainstFloor`, `floorStatus` | `backend/dashboard/lib/api/values.ts` | Fail-closed numeric and regulatory-floor presentation.         |
| Dashboard design and component rules             | `backend/dashboard/README.md`         | Current bank-product UI conventions and verification commands. |

## 4. Jurisdiction is data

- **Never hardcode country identity.** The global `jurisdictions` registry
  (`code → country, currency, locale, central bank, regulator short, portal, timezone`;
  NOT tenant-scoped) resolves through `banks.jurisdiction_code` and rides the bank API
  payload (`BankRead.jurisdiction`). Dashboard: BankContext binds it into `lib/format.ts`
  (`setActiveJurisdiction`) — use `fmtCurrency`/`fmtInt`/`fmtLocale()`/`regShort()`/
  `centralBankName()`/`currencyCode()`; never literal `'GHS'`, `'en-GH'`, `'BoG'`,
  `'Bank of Ghana'` in display code. Module-level constants evaluate before the binding —
  use jurisdiction-neutral wording there ("regulatory minimum", "supervisory severe"), not
  getter calls. Backend: services resolve names via `app/services/jurisdictions.py`
  (BSD-2/BSD-3 headers do); fact derivation reads `_Canonical.base_currency` (from
  `bank.currency`) for FX base-leg and curve selection. Deliberate exceptions
  (Ghana-factual content, keep literal): the BoG return-family artifacts — BSD
  templates/registry, ORASS/DBK rules, notice citations, the GHS ’000 unit convention in
  `SnapshotPreview`/`lib/templates.ts`, and the `sample_bank_seed` test fixture. Return
  families exist for Ghana only; other jurisdictions' families are roadmap work in
  `docs/product.md`.
- **A `bog_`-prefixed IDENTIFIER is not a jurisdiction leak — and must not be renamed:**
  the `bog_required_reserves` / `bog_excess_reserves` / `bog_excess_reserves_hqla` fact
  categories mean "central-bank reserves" in every jurisdiction and are load-bearing
  wire/DB keys (value-based `input_hash`, BSD line maps, goldens) — same rule as the
  `refinitiv` vendor id surviving its rebrand. Country identity in _matching logic_ is the
  real defect: a GL cash classifier that tests a literal token such as `"bog"` misses an
  SDI's `GL-1020 "Balances with Bank of Ghana"`, which then falls into `other_assets` and
  out of HQLA. Match on `fact_derivation._CentralBankNames` (the bank's own
  `central_bank_name` + `regulator_short` from the registry — never `country_name`, which
  would sweep "Government of Ghana bonds" into the cash line).
- **`banks.currency` and `banks.jurisdiction_code` are REQUIRED and carry no defaults**,
  so they cannot silently disagree (a bank created with `jurisdiction_code="NG"` must not
  report in cedis). Backend code resolves the unit through
  `jurisdictions.base_currency(bank)`, which deliberately has no fallback: an unset
  currency is a skipped decision at the creation site, not a Ghanaian bank. Never write a
  currency literal into bank-facing narrative — the guard suite
  `tests/services/test_jurisdiction_neutrality.py` scans the calculation modules for
  exactly that and is the cheapest place to catch the regression. On the dashboard,
  `fmtCurrency(value)` uses the active jurisdiction; passing a second argument OVERRIDES
  it, so pass it only when the currency is genuinely not the bank's own.
- Banks are created only by staff provisioning (`provision_institution`), which takes
  `currency` and `jurisdiction_code` explicitly; ingestion requires the bank to exist
  (`_get_bank_or_404`).

## 5. Feature layout

The risk service is moving from a layer-first tree (`app/services/`, `app/domain/`,
`app/models/`, `app/schemas/`, `app/features/`, `app/jobs/`) to one package per product
feature. New code goes in the target layout; existing code moves one feature per change.

- **Target shape.** `app/<feature>/` holds `public.py` (the cross-feature interface: re-exports
  only, no logic), `api/` (one router module per use case, today's `verb_noun` names kept),
  `service.py` or `service/`, `domain/` (pure engines under the same purity rule as
  `app/domain`), `models.py` or `models/`, `schemas.py` or `schemas/`, and `jobs.py` where the
  feature has worker handlers. A role is one module until it passes about 1,000 lines. The
  kernel stays in `app/core/`, `app/db/`, `app/storage/` and `app/integrations/`; the
  composition root is `app/main.py`, `app/worker.py`, `app/api/router.py`,
  `app/services/scheduler.py` and the `app.models` metadata registry. Kernel models
  (`app/models/{organization,audit_event,job}.py`) and `TenantContext` (`app/core/tenancy.py`,
  re-exported by `app.api.deps`) are shared by every feature.
- **Feature names and layers** live in `LAYERS` in
  `backend/tests/architecture/test_feature_boundaries.py`; `PEER_EDGES` defines the permitted
  same-layer directions and `FEATURE_RULES` assigns ownership in the existing layered tree.
- **Imports.** A feature imports only lower layers (same-layer only along a declared
  `PEER_EDGES` direction), and only another feature's `public` module or pure `domain`
  engines. The kernel imports no feature. Feature code never imports through the `app.models`
  aggregator; it is a registry, not an API.
- **Target test layout mirrors the source**: `app/<feature>/service.py` is tested under
  `tests/<feature>/service/`. Cross-cutting guards stay in `tests/architecture/` and shared
  helpers in `tests/support/`. `tests/conftest.py` keeps only the fixtures every feature shares
  (settings, database, session, client, storage fakes, demo tenants); a feature's fixtures live in
  `tests/<feature>/fixtures.py`, registered by the root `pytest_plugins`.
  `tests/architecture/test_test_layout.py` guards new top-level directory names and
  registered fixture ownership; its legacy layer-directory list shrinks as tests move.
- **The ratchet.** `test_feature_boundaries.py` assigns every `app/` module an owner and
  records today's violations in `feature_boundary_baseline.json`. A new violation fails, and so
  does a baseline entry that no longer occurs, so the baseline only shrinks.
  Record behaviour-neutral module moves in `backend/scripts/feature_module_moves.json`, the
  cumulative ledger consumed by the boundary guard: append `[old, new]` pairs of
  exact dotted module names in move order (package names omit `.__init__`). Keep earlier pairs
  when a module moves again; list each moved module, rather than package-prefix substitutions.
  The guard evaluates ownership and interfaces at current paths, then maps both dependency
  endpoints back through the ledger for baseline identity. A file move leaves baseline lines
  unchanged; a dependency fix deletes them.
  After fixing dependencies, regenerate the baseline from `backend/` with
  `uv run python -c "import tests.architecture.test_feature_boundaries as t; t.write_baseline()"`
  and review that the diff only deletes entries. `write_baseline()` owns the generated JSON's
  formatting.
- **Moving code.** A move PR runs `uv run python scripts/feature_moves.py move OLD NEW` from
  `backend/`, using dotted module or package names; a batch may supply multiple `OLD NEW`
  pairs. The codemod `git mv`s the files, records their moves in the ledger above, and rewrites
  imports, string patch targets, slash paths and doc references in the text files selected by
  `tracked_text_files()` in `backend/scripts/feature_moves.py`. This includes Dockerfile COPY
  paths and generated Python in Python, JavaScript and TypeScript strings; generated
  `packages/` files and the codemod's own source and tests are excluded. The boundary baseline
  retains its historical names as described above.
  It validates a batch before changing files; overlapping moves run as separate commands.
  It leaves no compatibility shim at the old path. After rebasing onto a move, in-flight
  branches run `uv run python scripts/feature_moves.py rewrite` from `backend/`;
  current and historical locations of each relative import must identify one canonical target
  or the rewrite refuses to proceed. Resolve ambiguous imports explicitly before retrying.
  Changed Python files get Ruff import sorting, plus Ruff formatting if they were formatted
  before. A move reports architecture-guard path literals matching fewer files afterwards;
  review and update those guards by hand so they retain their coverage.
  `uv run python scripts/feature_moves.py check` fails when a recorded module remains at its
  old path, a selected file needs rewriting, or a relative import cannot be resolved uniquely.
  The diff of a move PR is the renames plus the codemod's output; logic changes go in separate
  PRs.
- **What a move must not change.** Models share no `relationship()`, so SQLAlchemy flushes their
  rows in the order of each mapper's `module.ClassName`; `backend/app/db/flush_order.json` pins
  those keys so moving a model never reorders a flush (a new model adds its key). Metric
  `calculation_engine` ids are persisted in filed packages and stay frozen;
  `backend/app/domain/authority/engines.py` maps them to the engines' current locations.
  The codemod rewrites those locations while preserving the identifiers; the registry tests
  pin them against `backend/tests/domain/authority/frozen_engine_ids.json`.
  `backend/tests/architecture/feature_boundary_split_origins.json` freezes the exact pre-existing
  importer/target pairs affected by the model split. Only those pairs retain their historical
  target module and feature after reversing the move ledger; all other imports use current
  ownership. Its importer lists may only shrink, and the scanner rejects resolved entries
  until they are removed. The JSON stays outside the codemod's rewrite scope.
