# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- **`docs/product.md` is the master product roadmap** (source of truth for build
  sequencing, Phase 0 as-built anchor → Phase 7 enterprise). Sub-docs (rbac.md,
  data_engine.md, ai_engine.md, market_data_adapter.md, regulatory_reporting.md,
  storage.md, temenos_adapter.md) govern domain detail; product.md governs order;
  code wins over both. Phase numbers are per-document — cite `doc.md §N Phase X`,
  never a bare "Phase 2".
- **Coolify compose apps: never use dollar-brace variable interpolation in deploy compose
  files** (2026-07-21 incident: Coolify parses compose text — comments included — and
  auto-seeds a UI env row per reference; with required-with-message guards it stored the
  message text as VALUES and duplicated rows every deploy, corrupting the backend app's
  env store until the resource was recreated). Pattern: services load `env_file: .env`
  (Coolify writes it from its UI); fail-fast lives in the app's settings validators.
  Exception: build args (dashboard NEXT_PUBLIC_*) must stay interpolated — keep guards
  bare `:?` with no message text.
- **No seeded bank data — ever (order of 2026-07-21).** Every data point enters through
  the Data Engine (Excel/CSV upload, core-banking adapters, API push); a bank is created
  by its first ingestion. The primary DB was audited clean (100% ingestion-batch-traced).
  `POST /banks/seed-demo` exists ONLY as the hermetic test fixture behind
  `DEMO_SEED_ENABLED` (default off; tests/conftest.py enables it) — never enable it
  against the primary database, never add seeding paths to the UI, and never re-add
  seed CLI scripts.

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
- Financial review UI code lives under the removed `aequoros-web` SPA (see git history) and must call
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
- **Live-data invariant suite** (`backend/tests/live_data/`): read-only checks against the
  ACTUAL primary database — provenance (every canonical row ingestion-traced; the
  executable form of the no-seeding order), period-spine contiguity, fact coverage,
  live-metrics presence, sign-in capability. Opt-in:
  `LIVE_DATA_DATABASE_URL=<worker URL> uv run pytest tests/live_data` (BYPASSRLS worker
  URL for visibility, or set `LIVE_DATA_ORG_ID`). The session is server-side read-only —
  it cannot mutate what it certifies. Hermetic suite stays the home of mutation/logic
  tests; never point mutating tests at the primary DB.
- The primary database is the **remote Postgres** (`<postgres-host>:<port>/<database>`, credentials
  only in untracked `backend/.env`). Postgres-gated tests run against it via `TEST_DATABASE_URL`
  (each run creates and drops a `risk_service_test_<hex>` schema — the shared DB is safe). The
  default suite is hermetic: conftest sets `DATABASE_URL=""` (empty = unconfigured via a settings
  validator) so a developer's `.env` can never leak into tests. Remote gotchas: the single role
  has no BYPASSRLS (worker needs a granted role before running remotely), and ad-hoc `psql` must
  set the `app.organization_id` GUC or FORCE-RLS tables read as empty.
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
  leak canary enforces this). Vendor naming: the Refinitiv brand is retired (Eikon
  withdrawn 2025-06-30 → LSEG Workspace; the platform APIs are the LSEG Data Platform,
  formerly RDP) — internal vendor id stays `refinitiv` for wire/DB stability, user-facing
  labels read "LSEG (formerly Refinitiv)".
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
- SSO is AequorOS' **own OIDC relying party** — no third-party broker (Auth0 removed
  2026-07-20; never reintroduce `AUTH0_*`). Per-org connection in `sso_connections`
  (issuer, client_id, AES-256-GCM-sealed secret, allowed email domains; RLS-forced),
  managed in dashboard Settings → Authentication (secret write-only). The backend verifies
  every id_token via OIDC discovery + issuer JWKS (`verify_oidc_id_token`; RS256/ES256,
  `email_verified`, domain allow-list) and links **pre-provisioned** users
  (`auth_provider='oidc'`) — plus opt-in request-access JIT (`jit_enabled`): an
  allowed-domain first sign-in records a DEACTIVATED stub + 403 "awaiting approval";
  access exists only after an admin approves with an explicit role
  (`/auth/sso/access-requests`). Never let JIT auto-activate accounts — that was
  rejected 2026-07-20 as a data-leak path until RBAC group-mapping lands. The dashboard's NextAuth loads the client config through
  `GET /auth/sso/client-config`, gated by `SSO_INTERNAL_KEY` (same value on backend and
  dashboard; not in OpenAPI) — the single plaintext read path for the secret. Bank-IT
  runbook: `docs/sso-onboarding.md`; roadmap: rbac.md §15 Phase 2 (multi-connection +
  home-realm discovery — extend the existing code, don't rebuild).
- **Jurisdiction is data — never hardcode country identity (built 2026-07-23).** The
  global `jurisdictions` registry (`code → country, currency, locale, central bank,
  regulator short, portal, timezone`; NOT tenant-scoped; GH/NG/KE/ZA seeded) resolves
  through `banks.jurisdiction_code` and rides the bank API payload
  (`BankRead.jurisdiction`). Dashboard: BankContext binds it into `lib/format.ts`
  (`setActiveJurisdiction`) — use `fmtCurrency`/`fmtInt`/`fmtLocale()`/`regShort()`/
  `centralBankName()`/`currencyCode()`; never literal `'GHS'`, `'en-GH'`, `'BoG'`,
  `'Bank of Ghana'` in display code. Module-level constants evaluate before the
  binding — use jurisdiction-neutral wording there ("regulatory minimum",
  "supervisory severe"), not getter calls. Backend: services resolve names via
  `app/services/jurisdictions.py` (BSD-2/BSD-3 headers do); fact derivation reads
  `_Canonical.base_currency` (from `bank.currency`) for FX base-leg and curve
  selection. Deliberate exceptions (Ghana-factual content, keep literal): the BoG
  return-family artifacts — BSD templates/registry, ORASS/DBK rules, notice
  citations, the GHS ’000 unit convention in `SnapshotPreview`/`lib/templates.ts`,
  and the `sample_bank_seed` test fixture. Return families per jurisdiction are the
  unbuilt half (product.md §Phase 5 item 0).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
