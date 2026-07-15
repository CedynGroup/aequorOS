# AequorOS Codebase Conventions

Verified against the code on 2026-07-14. Companion to [ARCHITECTURE.md](ARCHITECTURE.md).
Match existing code exactly; do not introduce new patterns when one below already fits.

---

## 1. Python (backend/risk-service)

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

### Tests (`backend/risk-service/tests/`)

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
  `headers(org_id, user_id)` which returns `{"X-Org-Id": ..., "X-User-Id": ...}`.
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

## 2. Web (apps/aequoros-web)

### Component library — `src/components/ui.tsx` (single file, complete export list)

| Export | Usage |
| --- | --- |
| `Button` | `<Button variant="default|outline|ghost|danger" size="default|sm|icon">` — the only button. |
| `Input` | Styled `<input>`; pass `aria-label` when there is no visible label. |
| `Textarea` | Styled `<textarea>`. |
| `Label` | Uppercase micro-label `<span>` for form fields/sections. |
| `Badge` | `<Badge tone="neutral|success|warning|danger|info">` status chip. |
| `Panel` | Bordered surface `<section>`; the standard tab/card container. |
| `PanelHeader` | `title` + optional `meta` and `actions` row inside a `Panel`. |
| `Alert` | `<Alert title="..." tone="neutral|danger|warning">children</Alert>` for empty/info/error states. |
| `Skeleton` | Pulsing placeholder block; pair with `aria-label="Loading ..."` wrappers. |
| `Tabs`, `TabsList`, `TabsContent` | Radix Tabs re-exports (controlled via `value`/`onValueChange`). |
| `TabsTrigger` | Styled Radix trigger. |
| `Select`, `SelectItem` | Controlled Radix select: `<Select ariaLabel value onValueChange placeholder>` + `SelectItem value`. |
| `Checkbox` | Controlled Radix checkbox; requires `aria-label`. |
| `Switch` | Controlled Radix switch. |
| `Dialog`, `DialogTrigger`, `DialogContent` | Radix dialog; `DialogContent` takes `title` (+ optional `description`). |
| `DropdownMenu`, `DropdownMenuTrigger`, `DropdownMenuContent`, `DropdownMenuItem` | Radix dropdown re-exports. |
| `Tooltip` | `<Tooltip label="...">{trigger}</Tooltip>`. |

Styling uses Tailwind with CSS-variable tokens: `rgb(var(--border))`, `--surface`, `--muted`,
`--muted-foreground`, `--primary`, `--danger`, `--focus` (defined in `src/styles.css`). Compose
classes with `cn()` from `src/lib/utils.ts`. Do not add a second component library.

### API access — two sanctioned patterns, nothing else

Base URL: `apiBaseUrl()` in `src/lib/constants.ts` — `VITE_RISK_API_BASE_URL` env override,
default `http://127.0.0.1:8003/api/v1`. Tenant identity is a
`TenantHeaders = { orgId, userId }` object threaded through props.

**Pattern A — `riskApi` wrapper (`src/lib/api.ts`).** Adding a new endpoint:

1. Import the generated request/response types **and** their `FromJSON`/`ToJSON` codecs from
   `@aequoros/risk-service-api`.
2. Add a method to the `riskApi` object that calls
   `apiJson<T>(path, tenant, DecoderFromJSON, init?)` — `apiJson` injects `Accept`,
   `Content-Type`, `X-Org-Id`, `X-User-Id`, and normalizes failures into
   `ApiError { statusCode, code, message, response }` (check with `isApiError`).
3. Query strings go through the local `toQuery({ snake_case_param: value })` helper (drops
   `undefined`/empty); bodies are `JSON.stringify(SomethingToJSON(payload))`.

**Pattern B — generated API class (newer; see `src/features/liquidity/liquidity-client.ts`).**
Instantiate the generated class with
`new LiquidityApi(new Configuration({ basePath: apiBaseUrl().replace(/\/api\/v1\/?$/, "") }))`
and call its camelCase operations, passing `xOrgId`/`xUserId` explicitly. Wrap it in a small
feature-local client interface so components stay mockable. Per `CLAUDE.md`, financial-workspace
code must use `FinancialDataApi` this way. Prefer Pattern B for new verticals.

Never hand-roll payload shapes or duplicate OpenAPI types — regenerate the client instead.

### TanStack Query conventions (from `liquidity-tab.tsx` / `capital-tab.tsx`)

- `queryKey: [<resource-kebab-name>, tenant, caseId, ...discriminators]`, e.g.
  `["liquidity-summary", tenant, caseId, scenarioId, runId]`,
  `["calculation-runs", tenant, caseId, scenarioId, offset]`,
  `["capital-projections", tenant, caseId, attemptOffset]`. The whole `tenant` object is part of
  the key. Fixed-variant queries append a string discriminator (`"capital-active"`).
- Gate dependent queries with `enabled: Boolean(...)`.
- Mutations: `useMutation` + `onSuccess` → `queryClient.invalidateQueries({ queryKey: [prefix,
  ...] })` (prefix invalidation is used deliberately, e.g. `["capital-projections"]`), plus a
  `toast.success(...)` from `sonner`. Render mutation errors with `<ErrorPanel error={...} />`.
- Loading = `Skeleton` blocks; error = `ErrorPanel`; empty = `Alert`.

### Adding a console tab

1. Add the tab id to the `tabs` array in `src/lib/constants.ts` (this drives the `ConsoleTab`
   type and URL `?tab=` validation via `isConsoleTab`).
2. Create `src/features/<domain>/<domain>-tab.tsx` exporting `<DomainTab>` with props
   `{ tenant, caseId, mutationDisabled?, mutationDisabledReason? }`.
3. Register it in `src/features/risk-console/case-workspace.tsx`: a `lazy(() => import(...))`
   at top, a `<TabsTrigger value="...">` in the list, and a `<TabsContent>` wrapping the tab in
   `<LazyTabBoundary>`.
4. Colocate tests as `<name>.test.tsx` next to the source (Vitest + Testing Library; browser-mode
   variants use `.browser.test.tsx`).

### Demo-mode / read-only gating

`risk-console.tsx` holds `mockWorkspace` state; when on, list/case data comes from
`src/features/demo-data/demo-data.ts` and every mutating tab receives
`mutationDisabled={mockWorkspace || caseRetired}` with
`mutationDisabledReason={caseRetired ? "retired-case" : "demo"}`. Tabs must render an explanatory
`Alert` and suppress all mutation UI when disabled — **every financial mutation stays disabled in
demo mode** (CLAUDE.md requirement). Follow `liquidity-tab.tsx` for the
`readOnlyReason` cascade (`demo` / `retired-case` / `archived-scenario` / terminal finding).

### Formatting helpers

- `src/lib/money.ts` — `formatMoney(value: string, currency)` and
  `formatDecimal(value: string, fractionDigits)`. Inputs are **decimal strings** (the generated
  client surfaces backend `Decimal`s as strings); BigInt-based half-up rounding, locale-aware.
  Use these for all money/ratio display. (`src/lib/utils.ts` contains an older duplicate
  `formatMoney`; prefer `money.ts`.)
- `src/lib/utils.ts` — `cn` (clsx + tailwind-merge), `labelize` (snake_case → Title Case),
  `truncateId` (uuid → `8chars...4chars`), `formatJson`.
- `src/features/risk-console/format.tsx` — `StatusBadge`, `RiskBadge`, `DecisionBadge`,
  `relative(date)` (via `date-fns formatDistanceToNow`).
- `src/lib/persistent-state.ts` — `usePersistentState(key, default)` localStorage-backed string
  state. `src/lib/workspace-deep-link.ts` — `workspaceHash()` / `focusWorkspaceTarget()` for the
  `#...-record-id` deep links that evidence `source_url`s produce.

### Lint/type constraints

- `pnpm --filter @aequoros/aequoros-web lint` runs
  `oxlint src e2e vite.config.ts vitest.browser.config.ts playwright.config.ts --react-plugin
  --jsx-a11y-plugin --vitest-plugin --import-plugin --deny-warnings`. Warnings fail the build;
  jsx-a11y is enforced (label your inputs/selects). TypeScript is strict; `typecheck` = `tsc -b`.

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
| Full `ui.tsx` component set (table above) | `src/components/ui.tsx` | All UI primitives for new tabs. |
| `FindingReviewCard` | `src/features/findings/finding-review-card.tsx` | Shared severity/status card for any finding-review UI (used by liquidity + findings tabs). |
| `ErrorPanel`, `DataList`, `EmptyRow` | `src/shared/route-ui.tsx` | Error rendering (understands `ApiError`), definition lists, empty table rows. |
| `riskApi`, `apiJson`, `apiText`, `isApiError`, `TenantHeaders` | `src/lib/api.ts` | Pattern-A endpoint wrappers. |
| `liquidityReviewClient` | `src/features/liquidity/liquidity-client.ts` | Pattern-B reference: wrapping a generated `*Api` class. |
| `apiBaseUrl`, `tabs`, `ConsoleTab`, `isConsoleTab` | `src/lib/constants.ts` | Base URL + tab registry. |
| `formatMoney`, `formatDecimal` | `src/lib/money.ts` | Money/ratio display from decimal strings. |
| `cn`, `labelize`, `truncateId`, `formatJson` | `src/lib/utils.ts` | Class merging and misc formatting. |
| `StatusBadge`, `RiskBadge`, `DecisionBadge`, `relative` | `src/features/risk-console/format.tsx` | Case metadata display. |
| `usePersistentState` | `src/lib/persistent-state.ts` | localStorage-persisted UI state (tenant ids, toggles). |
| `workspaceHash`, `focusWorkspaceTarget` | `src/lib/workspace-deep-link.ts` | Evidence deep-link focus behavior. |
| `mockCase`, `mockCaseList` | `src/features/demo-data/demo-data.ts` | Demo-mode data (keep new tabs demo-safe). |
