# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Scenario resources live under `/api/v1/cases/{case_id}/scenarios`. Calculation
  readiness requires every active scenario to contain growth, expenses,
  cash-flow timing, credit-usage, and repayment-behavior assumptions, with each
  assumption explicitly reviewed after its latest edit.
- Regenerate scenario and other API contracts with
  `mise run risk-service:openapi-client`; validate the generated package with
  `pnpm --filter @aequoros/risk-service-api test`.
- Keep `packages/risk-service-api/src` excluded centrally from style linting and
  formatting; generated files must contain no inline suppressions, while type-checking,
  package tests, and freshness checks remain required. Client regeneration intentionally
  bypasses the formatting exclusion to normalize deterministic output.
- Financial review UI code lives under `apps/aequoros-web/src/features/financial` and must call
  `FinancialDataApi` from `packages/risk-service-api`; do not duplicate OpenAPI payloads or
  hand-roll financial workspace requests.
- Canonical institution, account, reporting-period, balance, cash-flow, obligation, and covenant
  mutations require a non-empty reason and return the record plus refreshed validation. Their
  review forms support manual entry and correction through the generated contracts.
- Keep every financial mutation disabled while demo mode is active. Constrain account and
  obligation statuses to generated contract values; automatic covenant compliance recalculation
  must omit `complianceStatus` so the backend derives it from the covenant inputs.
- The case-health header summarizes financial validation, scenario readiness, the latest forecast,
  active findings by severity, covenant compliance, and decision state. Keep its queries tenant- and
  case-scoped, reuse the owning tabs' cache keys, and make each summary navigate to and focus its tab
  exactly once.
- Validate web changes with `pnpm --filter @aequoros/aequoros-web typecheck`, `lint`, `test`, and
  `build`; deterministic financial review journeys are in `e2e/financial-review.spec.ts`.
- Balance-sheet forecast attempts live under `/api/v1/cases/{case_id}/calculation-runs`.
  Runs are immutable snapshots: reruns create a new row with current canonical
  financial data and reviewed scenario assumptions, while prior successful
  outputs and failed-run diagnostics remain available.
- Forecast snapshots use the latest effective balance reporting period on or
  before the requested as-of date. Only active obligations participate, and
  active obligations require both principal and outstanding amounts.
- Calculation history endpoints return paginated run summaries; fetch a run by
  ID for its immutable input snapshot and forecast outputs.
- Capital projection attempts live under `/api/v1/cases/{case_id}/capital-projections`
  and consume a successful calculation run. They persist period indicators and
  generated case findings with calculation-run, forecast-period, and input-hash evidence.
- Capital summaries return the latest successful projection, while
  `/capital-comparison` pairs the latest baseline and downside projections by period.
  The MVP pressure rules use equity-to-assets, liabilities-to-assets, and equity change;
  non-positive projected assets fail with named forecast-period diagnostics.
- Successful forecast runs automatically calculate deterministic liquidity metrics and generate
  tenant-scoped liquidity findings. Liquidity evidence locators bind forecast periods, canonical
  inputs, and reviewed scenario assumptions to the calculation input hash.
- Liquidity summaries and acknowledge/dismiss review actions live under
  `/api/v1/cases/{case_id}/liquidity`; reuse the shared case-finding review card in SPA analysis
  verticals. Chart classification reference lines must use the threshold and rule version
  persisted with the immutable analysis; treat absent metadata as a legacy unavailable state.
- Restore the fixed four-case presenter portfolio with `RISK_DEMO_DATABASE_URL=... mise run
risk-service:reset-demo`. The Core-insert reset is idempotent, deletes only the known demo
  tenant, and pre-populates forecast, liquidity, capital, finding, decision, and report inputs in
  explicit dependency order. The presenter journey is documented in `docs/demo-playbook.md`.
- Risk-console analysis charts use lazy-loaded Recharts components under
  `apps/aequoros-web/src/features/charts`. Keep generated DTO-to-series domain logic in pure
  adapters, preserve original decimal strings for labels, represent unavailable values as
  annotated gaps, and retain the authoritative tables alongside every chart.
