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
- Financial review UI code lives under `apps/aequoros-web (REMOVED — see ARCHITECTURE.md)/src/features/financial` and must call
  `FinancialDataApi` from `packages/risk-service-api`; do not duplicate OpenAPI payloads or
  hand-roll financial workspace requests.
- Canonical institution, account, reporting-period, balance, cash-flow, obligation, and covenant
  mutations require a non-empty reason and return the record plus refreshed validation. Their
  review forms support manual entry and correction through the generated contracts.
- Keep every financial mutation disabled while demo mode is active. Constrain account and
  obligation statuses to generated contract values; automatic covenant compliance recalculation
  must omit `complianceStatus` so the backend derives it from the covenant inputs.
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
  verticals.
- The live engine is two-tier (see ARCHITECTURE.md §3b): ingestion enqueues a debounced
  `pipeline_refresh` job that re-derives facts and upserts `live_metrics`/`live_findings` with
  zero `RegulatoryRun` writes, while scheduled/on-demand `official_run` jobs mint the immutable
  filing runs. Endpoints: `GET /banks/{id}/live-summary|freshness|alerts`,
  `POST /banks/{id}/refresh|official-runs`.
- The background worker claims jobs **across tenants**, so on RLS-forced Postgres it must run with
  a BYPASSRLS role — set `WORKER_DATABASE_URL` (the tenant-scoped app role sees zero queued rows).
  Falls back to `DATABASE_URL` for SQLite tests.
- Regulatory `input_hash` must stay **value-based**: the snapshot `facts` list excludes `fact.id`
  and is sorted by canonical JSON (`INPUT_SCHEMA_VERSION = "bank-facts-v2"`). The live engine
  re-derives facts (new UUIDs) on every refresh, so an id- or order-dependent hash would break
  official-run reproducibility. Never reintroduce `fact.id` into a `_build_snapshot`.
- Market data flows only through `app/adapters/market_data/` (see ARCHITECTURE.md §3c and
  docs/market_data_adapter.md). Every adapter pull delegates to `pull_runner.execute_pull` —
  the single writer of market-data canonical state; never persist market data elsewhere.
  Vendor catalogs carry only spec-documented identifiers (`supported: false` otherwise —
  never invent Bloomberg mnemonics or RICs), and raw vendor errors/fields must never reach
  bank-facing surfaces (classify via `errors.BankFacingErrorCode`; the contract suite's
  leak canary enforces this).
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
