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
`userId` values through `VITE_RISK_TENANTS`:

```bash
VITE_RISK_TENANTS='[{"name":"Configured Bank","orgId":"33333333-3333-4333-8333-333333333333","userId":"cccccccc-cccc-4ccc-8ccc-cccccccccccc"}]' \
  pnpm --filter @aequoros/aequoros-web dev
```

The configured list replaces the demo defaults. It must be non-empty, every
entry must have a non-empty name and valid UUIDs, and organization UUIDs must be
unique. Invalid configuration blocks the console with a configuration error.
This boundary can be replaced by an authenticated tenant directory after MVP.

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

The deterministic portfolio includes a non-compliant liquidity covenant for
Mariner Trust so the case-health summary and adverse-state journeys do
not rely on optimistic defaults.

When the API is unavailable, enable frontend demo mode from the console to use
populated, validated financial data, reviewed baseline and downside scenarios,
an immutable successful forecast, active and historical findings, covenant
status, and decision history. The case-health header and the Financial,
Scenarios, Forecast, Findings, and Decisions tabs use these read-only fixtures
without making workspace API requests.

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

## Case Health

The case detail header summarizes canonical financial validation, scenario
readiness, the latest forecast and its stable `Forecast #N` reference, active
findings by severity, historical finding count, covenant compliance, and the
current decision. Loading, unavailable, empty, healthy, warning, and adverse
states are explicit; an unavailable query is shown as unknown rather than
healthy.

Each summary is a keyboard-accessible link to its owning tab. Activating one
updates the route and focuses the selected tab panel once. The header shares
tenant- and case-scoped React Query keys with the owning tabs, so financial,
scenario, forecast, finding, and decision changes refresh the summary without
cross-case cache leakage.

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

## Forecast Review

Successful runs show a lazy-loaded balance-sheet trajectory chart for projected
assets, liabilities, and equity before the authoritative output table. Chart
labels and tooltips retain the API's original decimal strings; missing periods
or non-plottable values remain disconnected and are listed as annotated gaps.
An empty, unavailable, loading, or failed visualization does not hide the run's
tabular output. Run history remains contained at narrow widths, and wide
forecast output tables scroll horizontally instead of clipping their values.

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

Compatible comparisons include a lazy-loaded equity-to-assets chart for the
baseline and downside projections. The API remains responsible for determining
comparison compatibility, and the comparison table exposes the same baseline
and downside ratios and remains authoritative when the visualization is empty,
unavailable, loading, or fails to render.

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

The tab includes a lazy-loaded sources-coverage chart for per-period values and
the persisted classification threshold. The summary contract returns the
threshold and rule version used to classify that immutable run, so historical
reference lines never drift with frontend or current-rule changes. Analyses
created before that metadata was persisted explicitly show the chart as
unavailable. An authoritative per-period table retains the original coverage
decimals, threshold, rule version, and gap diagnostics; metrics and findings
remain available below it.

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
API error and comparison-diagnostic states, and verifies tenant isolation. These
journeys also smoke-test the forecast, liquidity, and capital SVG charts,
including the persisted liquidity threshold reference line.
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
- `src/features/risk-console/case-health-header.tsx`: case validation, scenario, forecast, finding, covenant, and decision summaries with owning-tab navigation
- `src/features/risk-console/case-workspace.tsx`: detail summary, case-health and demo-data orchestration, and overview, financial, scenarios, forecast, capital, liquidity, decisions, documents, findings, and report tabs
- `src/features/risk-console/format.tsx`: risk/status/decision badges and date formatting
- `src/features/risk-console/types.ts`: feature-local queue/search helper types
- `src/features/documents/documents-tab.tsx`: document upload-request, completion, parse, and download URL workflows
- `src/features/findings/findings-tab.tsx`: manual finding creation plus shared finding review, status updates, and read-only demo history
- `src/features/financial/financial-client.ts`: generated-client adapter for financial workspace reads, mapping, validation, and supported mutations
- `src/features/financial/financial-tab.tsx`: financial workspace loading, mapping, and revalidation controls
- `src/features/financial/financial-sections.tsx`: grouped records, validation navigation, source traceability, audit history, and mutation forms
- `src/features/scenarios/scenarios-tab.tsx`: scenario initialization, lifecycle, assumption editing and review, validation, readiness, and read-only demo scenarios
- `src/features/calculations/calculations-tab.tsx`: forecast start and rerun controls, polling, paginated run history, diagnostics, projected outputs, and read-only demo forecasts
- `src/features/decisions/decisions-tab.tsx`: decision recording and history with retired-case and demo-mode mutation guards
- `src/features/capital/capital-tab.tsx`: projection generation and history, indicators, scenario comparison, findings, and evidence review
- `src/features/liquidity/liquidity-client.ts`: generated-client adapter for liquidity summary and finding review requests
- `src/features/liquidity/liquidity-tab.tsx`: scenario/run selection, metric diagnostics, evidence links, and finding review states
- `src/features/charts/analysis-chart-adapters.ts`: pure generated-DTO adapters for forecast, liquidity, and capital chart series
- `src/features/charts/analysis-charts.tsx`: lazy-loaded Recharts visualizations, legends and annotated gaps, accessible SVG metadata, and reduced-motion behavior
- `src/features/charts/chart-shell.tsx`: visualization loading and failure isolation that preserves authoritative content
- `src/features/demo-data/demo-data.ts`: frontend-only queue fallbacks and internally consistent read-only case-health fixtures
- `src/shared/route-ui.tsx`: route-level empty, error, and data-list helpers
- `src/routes/risk-console.tsx`: thin route export for TanStack Router wiring

## Test Suite

Vitest tests are colocated with the module they protect:

- `src/lib/api.test.ts`: headers, error envelopes, API route/payload serialization
- `src/lib/constants.test.ts`: tenant-directory defaults, validation, normalization, and selection
- `src/lib/money.test.ts`: localized percentage separators, digits, and rounding
- `src/features/documents/documents-tab.test.tsx`: document controls, upload request payloads, lifecycle actions
- `src/features/findings/findings-tab.test.tsx`: finding create/update controls, payloads, and read-only demo data
- `src/features/financial/financial-client.test.ts`: generated client routing, headers, serialization, and mutation decoding
- `src/features/financial/financial-tab.test.tsx`: loading, mapping, revalidation, and refresh failure states
- `src/features/financial/financial-sections.test.tsx`: grouped review, validation focus, source traceability, audit history, and supported mutations
- `src/features/scenarios/scenarios-tab.test.tsx`: scenario loading, empty, error, lifecycle, validation, editing, review, save, cache-refresh, and read-only demo states
- `src/features/calculations/calculations-tab.test.tsx`: forecast loading, empty, running, failure, success, rerun, history, formatting, and read-only demo states
- `src/features/capital/capital-tab.test.tsx`: capital loading, empty, failure, success, comparison, evidence, pagination, and mutation-disabled states
- `src/features/liquidity/liquidity-client.test.ts`: generated client routing, headers, query filters, and review payloads
- `src/features/liquidity/liquidity-tab.test.tsx`: liquidity loading, empty, unavailable, historical-run, evidence, review, and error states
- `src/features/charts/analysis-chart-adapters.test.ts`: decimal preservation, missing periods, liquidity-threshold availability, and comparison-basis series states
- `src/features/charts/analysis-charts.test.tsx`: SVG points, labels, threshold, gap annotations, and unavailable states
- `src/features/charts/chart-shell.test.tsx`: lazy-loading fallback and chart-failure isolation
- `src/features/demo-data/demo-data.test.ts`: fallback/demo data filtering, detail construction, and internally consistent case-health fixtures
- `src/features/risk-console/case-health-header.test.tsx`: summary loading, empty, error, healthy, adverse, cache-refresh, and navigation states
- `src/features/risk-console/case-workspace.test.tsx`: case-health tab routing, single-focus behavior, and populated read-only demo tabs
- `src/routes/search.test.ts`: typed search-param parsing
- `src/features/risk-console/risk-console.test.tsx`: bulk action result grouping

Browser-mode Vitest tests use Playwright for DOM-heavy component interactions:

- `src/features/risk-console/risk-console.browser.test.tsx`: Radix organization and case select portal behavior, mobile top-bar overflow and queue-toggle accessibility, and dialog rendering in Chromium

The Playwright E2E suite includes `e2e/financial-review.spec.ts` for source
drilldown and the upload, map, validate, correct, retry, revalidate, cash-flow
entry, and covenant-entry journeys. `e2e/capital-projection.spec.ts` covers the
deterministic capital projection, comparison chart, finding-evidence, failure,
and tenant-isolation workflow. `e2e/risk-console.spec.ts` covers forecast-chart
rendering and the persisted liquidity-threshold reference line in its seeded
analysis journeys; it also verifies the compact scenario table, seeded breaching
case-health summary, console overflow at 1440x1000, and header overflow at
1280x800.
Component coverage retains the unavailable state for legacy analyses without
persisted threshold metadata.
