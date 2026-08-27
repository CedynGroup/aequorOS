# AequorOS Codebase Conventions

Verified against the code on 2026-07-14. Companion to [ARCHITECTURE.md](ARCHITECTURE.md).
Match existing code exactly; do not introduce new patterns when one below already fits.

---

## 1. Python (backend)

### Tooling (from `pyproject.toml`)

- **ruff**: `line-length = 100`, `target-version = "py313"`. Lint rule set:
  `select = ["E", "F", "I", "UP", "B", "SIM", "PL"]`, `ignore = ["PLR2004"]`. When a function
  legitimately needs many parameters, the existing code suppresses per-line with
  `# noqa: PLR0913` (and `PLR0915` for long orchestration functions) rather than restructuring.
- **basedpyright**: `typeCheckingMode = "standard"`, `pythonVersion = "3.13"`,
  `include = ["app", "tests", "alembic"]`; `reportAny`, `reportExplicitAny`, and the
  `reportUnknown*` family are disabled.
- Every module starts with `from __future__ import annotations`.
- Python 3.13 syntax is used freely: `type X = Literal[...]` aliases, `X | None`, `StrEnum`.

### SQLAlchemy models (`app/models/*.py`)

- SQLAlchemy 2.0 declarative style only: `Mapped[...]` + `mapped_column(...)`. No legacy
  `Column =` assignments, no `relationship()` (the codebase queries explicitly instead).
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
  `liquidity.calculate_metrics(periods)` so it is unit-testable; longer-term home is
  `app/domain/...` per `docs/architecture.md`.
- Storage access only through the `ObjectStorage` protocol
  (`app/integrations/storage/base.py`); `S3ObjectStorage` + `get_object_storage()` in
  `s3.py` is the sole boto3 call site. Never import boto3 in features/services.

### Tests (`backend/tests/`)

- Layout: `tests/api/` (HTTP-level, the default style), `tests/services/`, `tests/features/`,
  `tests/db/`, plus `tests/conftest.py`.
- **Databases**: default is a per-test SQLite file under `tmp_path` (FK pragma enabled). Setting
  `TEST_DATABASE_URL` (see `mise run risk-service:test-postgres`) makes each fixture create a
  throwaway Postgres schema `risk_service_test_<hex>` and drop it afterwards — same tests, both
  engines. Postgres-only behavior (RLS, advisory locks) is written to no-op on SQLite.
- **Fixtures** (conftest): `client` (no DB), `db_client` (TestClient with schema created via
  `Base.metadata.create_all`, demo tenants seeded, storage overridden with `FakeStorage`),
  `db_session`, `api_factories`, `fake_storage`, `tenant_ctx`, `test_settings`/`db_settings`.
- **Tenant constants** from `tests/api/helpers.py`: `ORG_1`, `ORG_2`, `USER_1`, `USER_2`, and
  `headers(org_id, user_id, roles, authorization_version)`, which returns a signed
  `Authorization: Bearer ...` access token. It defaults to the seeded user's current
  authorization version (`1`); stale-session tests pass an older value explicitly.
- **Factories**: `tests/api/factories/` package — `ApiFactories` bundles `CaseFactory`,
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

| Helper | Where | Use for |
| --- | --- | --- |
| `DbSession`, `Tenant`, `MutationTenant`, `Storage`, `TenantContext` | `app/api/deps.py` | Every route's session/tenant/storage wiring. |
| `record_event(db, ctx, *, event_type, entity_type, entity_id, details)` | `app/services/audit.py` | Audit trail for every meaningful mutation, same transaction. |
| `get_case_or_404` / `get_case_for_update_or_404` / `ensure_case_is_not_archived` / `ensure_status_transition_allowed` | `app/services/cases.py` | Tenant-scoped existence + state guards (template for `get_bank_or_404`). |
| `get_finding_or_404`, `list_findings`, `list_case_findings`, `create_case_finding`, `update_finding`, `apply_finding_update`, `is_liquidity_workflow_finding`, `list_finding_evidence` | `app/services/findings.py` | Generic finding CRUD/review; reuse for new engines' findings. |
| `calculate_metrics`, `generate_findings`, `lock_finding_publication`, `serialize_finding_publication` | `app/services/liquidity.py` | Template for deterministic metric + finding publication with advisory-lock serialization. |
| `MONEY`, `RATIO`, `MAX_STORED_MONEY`, `_money`/`_ratio` quantizers, `_snapshot_hash` | `app/services/calculations.py`, `capital.py`, `liquidity.py` | Money/ratio rounding constants and SHA-256 input hashing (copy the constants; keep values consistent). |
| `RISK_TYPES`, `FindingStatus`, `Severity`, `CaseStatus`, `FindingSource`, derived sets | `app/domain/risk_constants.py` | Shared enum values; extend here, not inline. |
| `ObjectStorage` protocol, `get_object_storage` | `app/integrations/storage/` | All object-storage access; override in tests via dependency_overrides. |
| `Base`, `UuidV4PrimaryKeyMixin`, `UuidV7PrimaryKeyMixin`, `TimestampMixin`, `utc_now` | `app/db/base.py` | Model building blocks. |
| `Settings` / `get_settings()` | `app/core/config.py` | Env config; add nested `BaseSettings` groups with env aliases. |
| `ORG_1/ORG_2/USER_1/USER_2`, `headers()`, `ApiFactories`, `FakeStorage`, `db_client` | `tests/` | Test tenancy, data factories, storage stubbing. |

### Frontend

| Helper | Where | Use for |
| --- | --- | --- |
| `configuration`, `apiBaseUrl`, `apiOrigin` | `backend/dashboard/lib/api/client.ts` | Generated-client setup and the single API-origin authority. |
| `setAccessToken`, `getAccessToken` | `backend/dashboard/lib/api/token.ts` | Expiry-aware bearer-token cache synchronized with NextAuth. |
| Generated `*Api` classes and wire types | `packages/risk-service-api` | Every supported tenant API request and response. |
| TanStack Query hooks | `backend/dashboard/lib/api/hooks.ts` | Shared server-state reads, mutations, keys, and invalidation. |
| `numOrNull`, `assessAgainstFloor`, `floorStatus` | `backend/dashboard/lib/api/values.ts` | Fail-closed numeric and regulatory-floor presentation. |
| Dashboard design and component rules | `backend/dashboard/README.md` | Current bank-product UI conventions and verification commands. |
