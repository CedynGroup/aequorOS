# AequorOS Web

Internal risk operations console for the risk-service API.

## Run

```bash
pnpm --filter @aequoros/aequoros-web dev
```

The app defaults to `http://127.0.0.1:8003/api/v1`. Override with:

```bash
VITE_RISK_API_BASE_URL=http://127.0.0.1:8003/api/v1 pnpm --filter @aequoros/aequoros-web dev
```

The organization selector defaults to the seeded demo tenants. Deployments can
supply their current tenant directory as a JSON array of `name`, `orgId`, and
`userId` values through `VITE_RISK_TENANTS`; this configuration boundary can be
replaced by an authenticated tenant directory after MVP. When the variable is
set, invalid or empty configuration blocks the console with a configuration
error instead of selecting a seeded tenant.

## Demo Data

Restore the four-case narrative portfolio, including reviewed scenarios,
forecast history, liquidity analysis, source evidence, decisions, and the
completed report case, with:

```bash
RISK_DEMO_DATABASE_URL=postgresql://postgres:postgres@localhost:15432/risk_service \
  pnpm --filter @aequoros/aequoros-web reset:demo
```

The command is an idempotent, transactional reset scoped to the fixed demo
tenant. It needs a local admin database role because application roles are
protected by row-level security; if seeding fails, the prior portfolio remains
intact. See the [ten-minute demo playbook](../../docs/demo-playbook.md) for the
presenter path.

The frontend-only fallback uses the same four borrower narratives and is a
read-only presenter workspace. In that mode, document, financial, scenario,
forecast, liquidity, capital, finding, and decision mutations are disabled.
Archived cases are likewise read-only. Document download links remain
available because they do not mutate case data.

## Console Navigation And Scenario Review

The top bar provides named demo organization and case selectors. Selecting an
organization also selects its seeded user context, clears the current case, and
returns to the case queue. The queue can be hidden so the selected case uses the
full workspace width; case detail routes start with it hidden, including at
mobile widths.

Scenario assumptions are presented in a compact table with label, type, value,
unit, review status, and actions. Ratio assumptions are entered as percentages
with a `%` suffix while the API continues to store decimal ratios; for example,
entering `5` persists `0.05`. Other recognized units appear as input suffixes.
Forecast horizons show a `years` suffix. Capital ratios and liquidity ratio
metrics use percentage formatting with up to two decimal places, except minimum
sources coverage, which remains a multiple such as `1.20x`.

## Financial Review

The Financial tab loads the case's canonical financial workspace through the
generated `FinancialDataApi` client. Reviewers can map a document or completed
document extraction, re-run validation, navigate open validation issues to the
affected field, inspect source-row metadata and raw values, and review the
manual edit audit history. Source rows without canonical record links remain
listed for review after refreshes and navigation.

Institutions, accounts, reporting periods, balances, cash flows, obligations,
and covenants support inline correction and manual entry. Every change requires
a non-empty reason, and a successful mutation immediately applies the validation
state returned by the API before refreshing the workspace. Failed submissions
retain their inputs and can be retried; a failed refresh after a successful
mutation can be retried without submitting the mutation again.

Account and obligation statuses are limited to the generated contract values.
Covenant compliance can be set explicitly or left on automatic recalculation;
automatic recalculation omits the compliance status so the backend derives it
from the covenant inputs.

Relationship fields use named institution, account, reporting-period, and
obligation selectors. Duplicate names are disambiguated with banker-readable
attributes rather than internal identifiers. Source drilldowns and edit history
also use neutral labels or resolved reviewer names instead of record and user
IDs.

When frontend demo mode is active, the entire financial workspace is read-only,
including backend-loaded records, mapping, and revalidation.

## Presenter-Safe Review

The queue and case overview show resolved assignee names and stable assessment
run references. Decisions and manual-edit history show reviewer names. Forecast
history, diagnostics, financial relationships, and capital evidence use
scenario or business labels instead of internal IDs; evidence links still deep
link to the exact immutable record.

The Report tab defaults to the HTML committee view, with a JSON toggle for
technical review. Both representations omit entity identifiers and redact UUIDs
that appear inside nested report evidence while preserving distinct aliases for
UUID-bearing object keys.

## Capital Review

The Capital tab lists immutable projection attempts and successful forecast runs
for active scenarios. It follows every page of the per-scenario latest-run list,
so an older active scenario remains selectable even when newer run history fills
the first page. Reviewers can generate a projection, page through attempt
history, inspect period equity and pressure indicators in the projection's
immutable reporting currency, compare the latest baseline and downside
projections, and review generated findings with their forecast evidence. The
comparison explains incompatible as-of dates, reporting currencies, or horizons
instead of presenting misleading deltas.

Projection and finding mutations are disabled in demo mode and for retired
cases. Loading, API failure, no-run, no-projection, failed-attempt, incomplete
comparison, and successful result states are rendered explicitly.

## Liquidity Review

The Liquidity tab uses the generated `LiquidityApi` client to review the latest
successful forecast for a scenario or a selected run. It displays minimum cash,
peak gap, sources coverage, credit reliance, and cash runway, including backend
diagnostics for unavailable metrics. Severity-ranked findings show rationale
and deep-linked evidence for forecast outputs, canonical financial inputs, and
scenario assumptions.

Open findings can be acknowledged or dismissed; dismissal requires a reason.
Mutation errors remain visible and successful reviews refresh both the
liquidity summary and the shared Findings tab. Archived scenarios and terminal
findings are read-only. Successful runs created before liquidity analysis was
introduced show a rerun prompt instead of implying that no risk was found.

## Checks

```bash
pnpm --filter @aequoros/aequoros-web typecheck
pnpm --filter @aequoros/aequoros-web lint
pnpm --filter @aequoros/aequoros-web test
pnpm --filter @aequoros/aequoros-web test:browser
pnpm --filter @aequoros/aequoros-web e2e
pnpm --filter @aequoros/aequoros-web build
```

## Playwright E2E

The E2E suite starts Vite automatically and expects a seeded risk-service API at
`http://127.0.0.1:8003/api/v1`. Its forecast journey initializes and reviews a
scenario, verifies projected output and a changed-input rerun, reviews the
generated liquidity metrics, findings, and evidence, then checks
persisted failure history, preservation of prior output, and tenant isolation.
The capital journey creates baseline and downside forecasts and projections,
reviews indicators and finding evidence, compares aligned scenarios, exercises
API error and comparison-diagnostic states, and verifies tenant isolation.
From the repository root:

```bash
cd apps/risk-service
docker compose up -d
mise run risk-service:bootstrap-db

cd ../aequoros-web
RISK_DEMO_DATABASE_URL=postgresql://postgres:postgres@localhost:15432/risk_service pnpm reset:demo

cd ../..
DATABASE_URL=postgresql+psycopg://risk_service_app:risk_service_app@localhost:15432/risk_service \
  CORS_ORIGINS=http://127.0.0.1:5173,http://localhost:5173 \
  RISK_STORAGE_BACKEND=s3 \
  RISK_S3_BUCKET=risk-local \
  RISK_S3_REGION=us-east-1 \
  RISK_S3_ENDPOINT_URL=http://localhost:9000 \
  RISK_S3_ACCESS_KEY_ID=minioadmin \
  RISK_S3_SECRET_ACCESS_KEY=minioadmin \
  RISK_S3_FORCE_PATH_STYLE=true \
  HOST=127.0.0.1 \
  PORT=8003 \
  mise run risk-service:dev
```

With the backend running:

```bash
pnpm --filter @aequoros/aequoros-web e2e
pnpm --filter @aequoros/aequoros-web e2e:headed
pnpm --filter @aequoros/aequoros-web e2e:ui
```

## Structure

```text
src/
  components/        shared local UI primitives
  features/          feature-owned UI, data helpers, and tests
  lib/               API, constants, persistence, utilities
  routes/            thin TanStack route entry points and search parsing
  shared/            cross-feature route UI helpers
  test/              Vitest setup and render helpers
```

The web app uses a feature-based split so each operational surface owns its UI and tests:

- `src/features/risk-console/risk-console.tsx`: route-level orchestration for tenant, selected case, filters, and queries
- `src/features/risk-console/shell.tsx`: sidebar and top-bar console chrome
- `src/features/risk-console/case-queue-panel.tsx`: queue filters, table, selection, and pagination
- `src/features/risk-console/bulk-actions.tsx`: bulk action dialog, mutation, and result rendering
- `src/features/risk-console/case-workspace.tsx`: detail summary, overview, financial, scenarios, forecast, capital, liquidity, decisions, documents, findings, and report tabs
- `src/features/risk-console/format.tsx`: risk/status/decision badges and date formatting
- `src/features/risk-console/types.ts`: feature-local queue/search helper types
- `src/features/documents/documents-tab.tsx`: document upload-request, completion, parse, and download URL workflows
- `src/features/findings/findings-tab.tsx`: manual finding creation plus shared finding review and status updates
- `src/features/financial/financial-client.ts`: generated-client adapter for financial workspace reads, mapping, validation, and supported mutations
- `src/features/financial/financial-tab.tsx`: financial workspace loading, mapping, and revalidation controls
- `src/features/financial/financial-sections.tsx`: grouped records, validation navigation, source traceability, audit history, and mutation forms
- `src/features/scenarios/scenarios-tab.tsx`: scenario initialization, lifecycle, assumption editing and review, validation, and readiness
- `src/features/calculations/calculations-tab.tsx`: forecast start and rerun controls, polling, paginated run history, diagnostics, and projected outputs
- `src/features/capital/capital-tab.tsx`: projection generation and history, indicators, scenario comparison, findings, and evidence review
- `src/features/liquidity/liquidity-client.ts`: generated-client adapter for liquidity summary and finding review requests
- `src/features/liquidity/liquidity-tab.tsx`: scenario/run selection, metric diagnostics, evidence links, and finding review states
- `src/features/demo-data/demo-data.ts`: frontend-only fallback/demo data helpers
- `src/shared/route-ui.tsx`: route-level empty, error, and data-list helpers
- `src/routes/risk-console.tsx`: thin route export for TanStack Router wiring

## Test Suite

Vitest tests are colocated with the module they protect:

- `src/lib/api.test.ts`: headers, error envelopes, API route/payload serialization
- `src/features/documents/documents-tab.test.tsx`: document controls, upload request payloads, lifecycle actions
- `src/features/findings/findings-tab.test.tsx`: finding create/update controls and payloads
- `src/features/financial/financial-client.test.ts`: generated client routing, headers, serialization, and mutation decoding
- `src/features/financial/financial-tab.test.tsx`: loading, mapping, revalidation, and refresh failure states
- `src/features/financial/financial-sections.test.tsx`: grouped review, validation focus, source traceability, audit history, and supported mutations
- `src/features/scenarios/scenarios-tab.test.tsx`: scenario loading, empty, error, lifecycle, validation, editing, review, and save states
- `src/features/calculations/calculations-tab.test.tsx`: forecast loading, empty, running, failure, success, rerun, history, and formatting states
- `src/features/capital/capital-tab.test.tsx`: capital loading, empty, failure, success, comparison, evidence, pagination, and mutation-disabled states
- `src/features/liquidity/liquidity-client.test.ts`: generated client routing, headers, query filters, and review payloads
- `src/features/liquidity/liquidity-tab.test.tsx`: liquidity loading, empty, unavailable, historical-run, evidence, review, and error states
- `src/features/demo-data/demo-data.test.ts`: fallback/demo data filtering and detail construction
- `src/routes/search.test.ts`: typed search-param parsing
- `src/features/risk-console/risk-console.test.tsx`: bulk action result grouping

Browser-mode Vitest tests use Playwright for DOM-heavy component interactions:

- `src/features/risk-console/risk-console.browser.test.tsx`: Radix organization and case select portal behavior, mobile top-bar overflow and queue-toggle accessibility, and dialog rendering in Chromium

The Playwright E2E suite includes `e2e/financial-review.spec.ts` for source
drilldown and the upload, map, validate, correct, retry, revalidate, cash-flow
entry, and covenant-entry journeys. `e2e/capital-projection.spec.ts` covers the
deterministic capital projection, comparison, finding-evidence, failure, and
tenant-isolation workflow. `e2e/risk-console.spec.ts` also verifies the compact
scenario table and that the console has no horizontal overflow at 1440x1000 and
1280x800 viewports.
