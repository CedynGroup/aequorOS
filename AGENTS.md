# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- **`docs/product.md` is the master product roadmap** (source of truth for build
  sequencing, Phase 0 as-built anchor → Phase 7 enterprise). Sub-docs (rbac.md,
  data_engine.md, ai_engine.md, market_data_adapter.md, regulatory_reporting.md)
  govern domain detail (storage.md and temenos_adapter.md retired 2026-08-09);
  product.md governs order;
  code wins over both. Phase numbers are per-document — cite `doc.md §N Phase X`,
  never a bare "Phase 2".
- **Subdomains are product SEGMENTS, not environments (2026-08-03).** The
  authenticated bank product is `bank.aequoros.com` (renamed from the neutral
  `app.` while there were still zero SSO customers — the migration cost is
  re-registering two OIDC redirect URIs with every bank's IT department, so it
  only ever gets more expensive). `corp.aequoros.com` is reserved for corporate
  treasury. Marketing stays on the apex, `api.` is the backend, `bao.` is
  OpenBao. The segments are genuinely different products over shared engines,
  not one app with a flag: of the six modules, FTP, Basel capital and IRRBB do
  not transfer to a corporate at all, liquidity transfers in name only (LCR/NSFR
  are Basel ratios, corporate liquidity is cash and covenant headroom), and the
  whole regulatory spine — BoG return families, ORASS, filing attestation — is
  bank-only. The reusable value lives in `app/domain/*`, which is pure and must
  stay that way. When the corporate entity lands, make it a SIBLING of `banks`
  (a `CO-` platform id alongside `BK-`/`OR-`), never a nullable-heavy `banks`
  row — a corporate has no licence, no jurisdiction regulator, no return family.
  Changing the host touches `AUTH_URL`, `CORS_ORIGINS`, and `NEXT_PUBLIC_LOGIN_URL`
  on BOTH Coolify apps; the last is a **build arg** inlined at compile time, so
  it needs a rebuild, not a restart.
- **Staff control plane (built 2026-08-09..11; specs docs/internal/developer.md +
  staff_UI.md, both carry dated as-built notes).** The operator API is the backend's
  THIRD entrypoint (`app/operator/`, uvicorn `app.operator.main:app` :8100, compose
  service `risk-operator`; NEVER mounted on the tenant API — route-isolation test pins
  it) with a cross-tenant BYPASSRLS session; the console is the separate `console/`
  Next.js app (console.aequoros.com, all traffic via its `/api/op` proxy). Staff auth
  mirrors the client model: email+password against GLOBAL `operator_users` (separate
  from tenant identity by design; `operator_admin` = super admin, founder seeded),
  OIDC SSO secondary, dev bearer token non-production-only. Tenant onboarding runs as
  a saga through `provision_institution`; every operator mutation lands in append-only
  `operator_audit_log`. Workforce domain membership is identity evidence, not
  authorization: OIDC authentication requires a matching active `operator_users` row
  and always takes its explicit role from that row; unknown or inactive identities get
  the same generic 401 as any other invalid operator credential.
- **Market research desk (built 2026-08-09..11; spec docs/internal/
  AequorOS_Market_Data_and_Curve_Platform.md — its as-built header + calibration
  deviation are authoritative).** Desk-as-vendor: approved determinations publish into
  EVERY tenant through `pull_runner.execute_pull` as vendor `aequor_desk` (zero quota,
  AEQ.* curve names so vendor rows coexist — supersession keys ignore source). Global
  `desk_*` tables: methodology register (Track-1 weekly application vs Track-2
  versioned parameter changes, maker-checker everywhere), bitemporal determinations,
  silver captures. **Rates-first weekly flow (2026-08-11):** `desk_capture` stages a
  pre-computed **draft** only (never auto-submits); Analyst reviews/adjusts then
  submits; Supervisor approves. Determination-scoped `research_adjustments` (override /
  additive_bps / assumption_note + rationale) enter `package_digest` and do not rewrite
  the methodology register. Split QA: `rates_qa_passed` gates approve/submit/publish of
  rates; `curves_qa_passed` is advisory for rates publish (curve scopes omitted when
  false). Quant lib `app/domain/curves/` is pure. Nightly job behind
  `DESK_CAPTURE_ENABLED`. **Entitlements (spec §10):** `market_data_entitlements`
  grants org × dataset (tiers core/standard/premium); default standard when no rows;
  publish + market-data reads filter AEQ curves / GHS indices accordingly.
  **Stage 3** credit curve `AEQ.GHS.CORP` from liquid GFIM corporate yields when present;
  **Stage 4** true OIS via methodology `discounting_mode=ois_bootstrap` + `GHS.OIS.*`
  (falls back to synthetic AGD). Capture snippet viewer: `GET .../captures/{id}/content`.
  Engines: `get_discount_curve` prefers AEQ.{ccy}.OIS — EVE/duration discount on it when
  published, byte-identical fallback otherwise (the golden suites prove the fallback;
  never edit goldens to make dual-curve changes fit).
- **Coolify compose apps: never use dollar-brace variable interpolation in deploy compose
  files** (2026-07-21 incident: Coolify parses compose text — comments included — and
  auto-seeds a UI env row per reference; with required-with-message guards it stored the
  message text as VALUES and duplicated rows every deploy, corrupting the backend app's
  env store until the resource was recreated). Pattern: services load `env_file: .env`
  (Coolify writes it from its UI); fail-fast lives in the app's settings validators.
  Exception: build args (dashboard NEXT_PUBLIC_*) must stay interpolated — keep guards
  bare `:?` with no message text.
- **Coolify compose apps get ONLY the compose file on the host — never bind-mount a
  repo file** (2026-07-26, two failed OpenBao deploys). The Docker Compose build pack
  materialises the normalised compose plus its own `.env`/`README.md`; the repository is
  not checked out (that is the off-by-default "Preserve Repository During Deployment"
  toggle). A `- ./x.conf:/etc/x.conf` bind therefore has no source, Docker CREATES it as
  an empty directory, and the created directory then blocks any corrected checkout — so
  fixing the path alone cannot recover it (needs an `rmdir` on the host, with the
  container stopped, or it is recreated on the next restart). `create_host_path: false`
  does not save you: Coolify rewrites long-syntax mounts to short form and drops it.
  Put config INSIDE the compose (a `command:` heredoc) — `deploy/openbao/` is the
  worked example.
- **Institution identity is the platform ID — no UUIDs for banks/orgs (epoch 2026-07-24).**
  `organizations.id` (OR-XXXXXXXX) and `banks.id` (BK-XXXXXXXX) are short Crockford
  base32 codes generated by `app/services/public_ids.py` via the model defaults — the
  primary key, API path token, auth `org` claim, RLS GUC value, and UI identity. One
  identity, no aliases: never reintroduce UUID columns or a separate "public id" for
  these two entities (every other entity keeps UUID PKs). The hermetic fixture pins
  `BK-SAMP0001`/`OR-DEM00001` (tests + e2e mint against those); real tenants get
  generator codes at row creation. Migration `202607240025` performed the epoch
  (single-pass ALTER TYPE; legacy UUIDs archived in `platform_id_legacy_map`;
  pre-epoch `audit_events.entity_id` values keep their historical UUID text; RLS
  policies compare text — no `::uuid` casts). Pre-epoch regulatory run input hashes
  embed the old UUID string and stay internally consistent with their stored
  snapshots; new runs hash the platform ID.
- **Integration keys are the bank-middleware credential (built 2026-07-24).** Admin
  generates a revocable `aeq_live_…` key (Data Engine → API Push); it binds to a per-key
  service account (`users.auth_provider='service'`, analyst role) and works as a plain
  bearer value — `app/services/integration_keys.py`, auth branch in `app/api/deps.py`.
  Only the SHA-256 hash is stored (raw key shown once); `integration_keys` is
  deliberately NOT RLS-forced (pre-auth global hash lookup; hashes+metadata only —
  keep it that way and keep endpoints org-filtered). Public contract:
  docs/API_INTEGRATION.md §1.
- **Authorization foundation (built 2026-08-25; `backend/docs/authorization_foundation.md`).**
  New policy authority is an indivisible `authorization_bindings` row: principal/type +
  static bundle + explicit organization/institution/module/sensitivity scope + provenance
  and lifecycle. Rows OR only after every dimension within a row ANDs; explicit `all`
  values provide broad module/sensitivity scope, and organization-wide institution
  coverage is named.
  The evaluator is deny-by-default, ignores scalar role/token permission claims, returns an
  audit-ready trace, and accepts global condition vetoes. This is shadow-only: migration
  `202608250044` backfills no bindings or Owner/Admin authority. Token `authv` enforcement is
  live: pre-migration/stale tokens 401; every future role/scope/status/security mutation must
  call `authorization.invalidate_user_authorization` in-transaction to bump the user version
  and revoke refresh families.
- **No seeded bank data — ever (order of 2026-07-21).** Every data point enters through
  the Data Engine (Excel/CSV upload, core-banking adapters, API push); a bank is created
  by its first ingestion. The primary DB was audited clean (100% ingestion-batch-traced).
  There is **no seeding route at all**: `POST /banks/seed-demo` and its
  `DEMO_SEED_ENABLED` flag were deleted in `2dc359f` — this file asserted they still
  existed until 2026-08-22, and `grep` finds neither anywhere under `backend/app/`.
  `tests/api/test_banks.py::test_seed_route_is_retired` pins that the path resolves to
  no handler for any role or tenant. Never add seeding paths to the UI, and never
  re-add seed CLI scripts.

- **The reporting date is the REGULATOR's — never derived from ingestion (corrected
  2026-08-23).** A return's reporting dates come from its `ReturnDefinition` (cadence +
  BoG anchor conventions) through the ONE authority
  `app/services/regulatory_reporting/anchors.py`, which touches no tenant data; the
  calendar and the Returns workspace both bind to it, so they cannot disagree.
  `bank_reporting_periods` is the KEY FOR ONE COMPUTED FACT SNAPSHOT — created by the
  data path when a book arrives with an as-of date — and must never again be offered as
  the user's reporting-date list. It was, and that made BoG's calendar a function of
  ingestion cadence: 6 of the 22 BSD forms are weekly (Friday close), generation matched
  `period_end` exactly, and the reference tenant had **19 Friday period-ends against 517
  Fridays** in its span (17 of them only because the month ended on a Friday) — 96% of
  weekly filing dates unselectable, and a tenant that had ingested nothing showed an
  EMPTY calendar. Direction, pinned by `tests/services/test_reporting_anchors.py`:
  `ReturnDefinition -> reporting date -> snapshot lookup`. The snapshot match is **exact
  for every cadence** (`common.get_snapshot_for_reporting_date`) — the daily
  "latest period ending on or before" fallback was a fail-open that would file a
  month-old book as a business day's position; a miss is `no_computed_position` (409)
  naming the date required and the nearest earlier one, which is reported and NEVER
  substituted. An anchor with no data is still listed (`data_status='awaiting_data'`) —
  the deadline is BoG's and runs regardless. `period_start` stays day-1-of-month: it is
  the fiscal month-to-date window BSD7 (YTD), BSD8 (opening balance) and
  `implied_rating` read, not filler.
- **Official BoG BSD returns are generated from the templates themselves (built 2026-08-15;
  registry `docs/bog_returns/00_full_return_registry.md`).** Every workbook under
  `docs/reporting/` (BSD1…BSD17, 24 files / 76 sheets) is a registered return (family `bsd`,
  generator `bog_form`, `backend/app/services/regulatory_reporting/bog_forms/`). The committed
  `layouts/*.json` ARE the official structures (regenerate ONLY with
  `scripts/extract_bog_templates.py` from `docs/reporting/` — needs LibreOffice); line maps bind
  official INPUT cells to named source resolvers (`linemaps/<form>.py`, extra resolvers only in
  `sources_ext/<form>.py`); the engine then **evaluates the templates' own formulas**
  (`formulas.py`: SUM/IF/+−×÷/%/`[n]Sheet!` external links — 100% of 5,903 cells) so every
  roll-up is BoG's — never re-implement or "simplify" a BoG line, never bind a formula cell.
  Export = THREE artifacts per sealed run: `pdf` (values — the BoG submission package),
  `xlsx`/`xlsx_official` (official layout, values-only, sheets protected — audit twin),
  `xlsx_working` (official layout with the template's LIVE formulas, labelled WORKING COPY,
  never filed/signed; BSD forms only; migration 202608160015) — with a "Completion notes" sheet;
  input cells with no honest source are `input_required`/`unmapped`, never dropped. Blank data
  grids (no `0` placeholder) are bound with `grid_lines`, captured inputs with `leaf_lines`.
  Legacy recode (migration `202608150013`): the pre-template `BSD2`(CAR)/`BSD3`(LCR) entries are
  now `CAR-RWA`/`LCR-NSFR`; the `BSD-MONTHLY` placeholder is retired. Weekly returns anchor on
  Friday close (Guide fixes cadence not weekday). Gate: `tests/services/test_bog_forms_framework.py`
  + `tests/services/bog_forms/`; matrix `scripts/bog_coverage_matrix.py`.
- **Phase 2 (product.md §Phase 2) is fully built (2026-08-08).** All 11 LMTD
  appendix tables; per-currency gaps + `usd_funding_stress` (snapshot
  `bank-facts-v3`); server-side EWI/CFP with the ¶74 notification
  (`/banks/{id}/liquidity/ewis|cfp`); reverse stress (module
  `reverse_stress`); STRESS-PACK return (family `stress`, event-driven);
  IFRS 9 ECL (`app/domain/capital/ecl.py`; active only when `ecl_exposure`
  facts AND the `ecl-assumptions` register exist — otherwise the ingested-
  provisions path is byte-identical) + CRM haircuts (`crm_collateral` facts,
  Basel ¶151 code defaults + `crm-haircuts` register); ICAAP capital plan +
  quarterly ILAAP snapshots; examiner role (ladder position analyst >
  examiner > viewer — reads everything, no mutation gate admits it);
  BSD-MONTHLY / LAS-QUARTERLY are registry+calendar REAL but generate
  `template_pending` until the official forms land (never infer a BoG
  layout). The executable completion proof is
  `tests/services/test_phase2_full_report_proof.py` — every registered
  return generates + exports (or refuses by design) over the full official-
  run sweep; keep it green.
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
- **To assess a tenant's health, read what the PLATFORM computed — never call
  `derive_facts` yourself (2026-08-23).** The two tiers behave differently by
  design when a book does not reconcile: `derive_current_facts` (live) plugs the
  gap, stamps the fact `status="blocked"` and KEEPS SERVING, because an operator
  has to see a broken book to fix it; `derive_facts` (official) REFUSES, because
  a date that cannot produce a filable book must produce nothing. **A refusal
  from the official path is therefore not a fault signal** — it is the
  fail-closed design working, and a date with e.g. positions but no same-date GL
  is genuinely not filable. Reading it as breakage cost a full session: gaps of
  "86% of assets" were reported on two tenants and a data withdrawal was
  recommended for the reference tenant, while `live_metrics` said `ready`
  throughout and nothing was wrong. Health checks read `live_metrics` /
  `GET /banks/{id}/live-summary|freshness|alerts`, or the module's own service
  (`sdi_readiness`, `sdi_views`). `tests/architecture/test_derivation_plane_boundary.py`
  pins the caller allow-list; only `pipeline.run_official`,
  `data_activation.activate_bank_data` and `history_loader` may call the filing
  derivation.
- **Long-lived local processes serve STALE CODE and the port check will not save
  you (2026-08-23).** Four backend processes were running from one checkout —
  one a week old on `:8011`, one from three hours earlier — all against the
  primary. Port binding was never violated (uvicorn `--reload` shares one socket
  between supervisor and child), because the stale instance was on a DIFFERENT
  port. And a port conflict would not have helped: the damage is done by the
  **in-process live-engine worker thread**, which needs no port, polls the shared
  `jobs` table and writes `live_metrics` with whatever code its process holds. A
  new feature can therefore be verified green in a fresh process while the app
  serves the old behaviour from an old one. Same hazard as the shared prod/local
  `jobs` table below, entirely local.
  **The standalone worker is the one that bites, and it has NO `--reload`.**
  `python -m app.worker` is a separate process from `fastapi dev`; it never
  reloads on a code change, and it is what writes `live_metrics`. A cleanup that
  greps only `fastapi dev|uvicorn|app.main` MISSES it — that exact grep was used
  on 2026-08-23 to declare the environment clean while a worker from two hours
  earlier kept serving stale results for another half hour. Use the full pattern
  and check `lstart` against your edits:
  ```
  ps -eo pid,lstart,command | grep -E "fastapi dev|uvicorn|app\.main|app\.worker|app\.operator" | grep -v grep
  ```
  Restart the worker after ANY change to a service it dispatches
  (`fact_derivation`, `implied_rating`, the module engines) or its output is a
  lie about your code.
- The background worker claims jobs **across tenants**, so on RLS-forced Postgres it must run with
  a BYPASSRLS role — set `WORKER_DATABASE_URL` (the tenant-scoped app role sees zero queued rows).
  Falls back to `DATABASE_URL` for SQLite tests.
- **The stale-job reclaim window is per job type (2026-08-22).** `reclaim_stale` requires its
  window to EXCEED the longest legitimate handler runtime or it reclaims a live job and runs it
  twice — which is exactly what happened: `etl_dedup` measurably ran **2h02m** against the 900 s
  `WORKER_STALE_JOB_SECONDS` default, was marked "worker presumed dead" mid-flight, and executed
  concurrently with itself. A handler that outgrows the fleet default gets an entry in
  `job_queue.STALE_AFTER_OVERRIDES_SECONDS`, **never a bigger global number** (the global also
  governs how fast a genuinely dead worker's jobs come back). Setting the default asserts every
  unlisted type finishes inside it — the config comment names them. When a job exhausts
  `max_attempts` nothing re-enqueues it: the recovery surface is
  `GET /operator/v1/jobs/stuck-dedup` (fleet board, read) +
  `POST /operator/v1/tenants/{org}/fix/redrive-dedup` (session-gated, audited), and it is
  manual on purpose — the four stranded batches failed for three unrelated reasons.
- **CI enforces every surface (2026-08-22).** `risk-service.yml` (backend), `dashboard.yml`
  (typecheck + **lint** + **test** + build) and `web.yml` (`frontend` lint+build, `console`
  typecheck+test+build). Before this, `frontend/` and `console/` were in no workflow and the
  dashboard's fail-open guard and SSRF egress guard were unenforced. Each workflow's header
  comment is its gate inventory — keep it accurate. `console` is deliberately NOT lint-gated
  (no ESLint dependency or config in that workspace); see ARCHITECTURE.md §8.
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
- **Anything built with `Base.metadata.create_all` runs no migration and no worker, so it
  must seed what those two would have written** (2026-08-22). That is the hermetic pytest
  suite AND the Playwright stack (`scripts/e2e_bootstrap.py`). Two shared fixtures own it:
  `tests/fixtures/reference_data.py` seeds every GLOBAL registry from the same catalogues
  the migrations read (`jurisdictions`; `institution_types.seed_rows`;
  `regulatory_parameters.seed_rows`) — add a new registry there once and both callers get
  it — and `tests/fixtures/live_plane.py` stands in for the worker's `pipeline_refresh`,
  because every Treasury/ALM cockpit reads `current_financial_facts`, which only the worker
  writes. Skip either and the failure is late and misleading: a missing registry surfaces as
  a fail-closed 409 naming a seed migration, a missing live plane as "no computed data yet"
  on every module page. Full prerequisites (object storage included):
  `backend/dashboard/README.md` §End-to-end.
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
  dashboard; not in OpenAPI) — the single plaintext read path for the secret. **Two**
  redirect URIs must be registered at the IdP: `/api/auth/callback/sso` (sign-in) and
  `/api/attestation/step-up/callback` (signing step-up); registering only the first
  yields working sign-in with certification failing at re-authentication. Bank-IT
  runbook: `docs/sso-onboarding.md`; roadmap: rbac.md §15 Phase 2 (multi-connection +
  home-realm discovery — extend the existing code, don't rebuild).
- **Attestation / e-signature (built 2026-07-25; spec `docs/attestation_esignature.md`).**
  Signing is REQUIRED for every return by default (`default_policy`:
  `require_signature=True`, `require_signed_pdf=True`, preparer+approver, no family
  exemptions — changed 2026-07-25 on the founder's call). What BoG demands *of the
  artifact* stays unconfigured-by-default (spec §8 C1–C4: officer titles stay unset);
  what the institution demands *of itself* before filing is the product. A deployment
  that cannot sign therefore cannot file — `ensure_signing_configured` raises
  `signing_not_configured` naming the settings, and in production `/health/ready` 503s
  (boot only WARNS — an earlier boot refusal locked out the admin who could fix it).
  Banks relax per return in Settings (an audited PUT); tests use
  `tests/factories/attestation.relax_signing`. `ATTESTATION_ESIGN_REQUIRED=0` (default 1)
  is the deployment-wide kill-switch: applied after policy resolution
  (`attestation/policy.py::_apply_esign_kill_switch`, `source="esign_disabled"`), it
  suspends the requirement everywhere — configured mandatory rows go dormant, every
  return takes the bare maker-checker approval path, and re-enabling restores the rows
  unchanged.
  Fields are placed on the document (template per return, package override) from a typed
  palette — one `signature` per role plus any number of `name`/`title`/`initials`/
  `date_signed` boxes, because a BoG attestation block asks each officer for four things.
  Non-signature boxes are AcroForm TEXT fields whose value is DERIVED from the signature
  record (`SignatureAppearance.derived_values`), never sent by a client, and each kind has
  its own derived floor (`pdf_signing.MIN_BOX_SIZES`) — the old single 185×61 survives only
  as the threshold at which the four evidential lines are drawn as a caption. DocMDP means
  **every field must exist before the first signature**, so the preparer places the
  approver's boxes too; each role's values are filled in the SAME incremental update as
  that role's signature (a separate earlier revision makes pyHanko's in-place-appearance
  rule convict an untouched locked field), and `Sig_Preparer` carries a FieldMDP
  `/Exclude` lock over everything but the approver's fields. Three digests, all value-based like
  `input_hash`: `content_digest` (strips volatile `generated_at`), `register_state_digest`
  (master-data returns), `certification_digest` (what every signer signs) —
  `app/services/attestation/digests.py`; never add volatile fields. Signer IDs (`SGN-` +
  16 Crockford) are HMAC-derived from the user UUID under `SIGNER_ID_PEPPER` then
  **persisted as the authority** — rotating the pepper must never re-derive an existing
  identity. Append-only is *tiered* by DB trigger (migration `202607250027`):
  `audit_events` blocks UPDATE+DELETE; signature/identity/artifact-version tables block
  UPDATE only (DELETE reachable via package CASCADE) — see spec §9 D1 for why. Step-up:
  password re-entry, or an SSO redirect through the three Next.js server routes under
  `dashboard/app/api/attestation/` — the id_token and the signing authorisation are
  server-only by design (HttpOnly cookie, spent by a route), so never move either into
  the session or a client fetch.
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
  **A `bog_`-prefixed IDENTIFIER is not a jurisdiction leak — and must not be
  renamed** (2026-08-22): the `bog_required_reserves` / `bog_excess_reserves` /
  `bog_excess_reserves_hqla` fact categories mean "central-bank reserves" in every
  jurisdiction and are load-bearing wire/DB keys (value-based `input_hash`, BSD
  line maps, goldens) — same rule as the `refinitiv` vendor id surviving its
  rebrand. Country identity in *matching logic* is the real defect: the GL cash
  classifier tested the literal token `"bog"`, so the SDI's `GL-1020 "Balances
  with Bank of Ghana"` fell into `other_assets` and out of HQLA. Match on
  `fact_derivation._CentralBankNames` (the bank's own `central_bank_name` +
  `regulator_short` from the registry — never `country_name`, which would sweep
  "Government of Ghana bonds" into the cash line).
  **`banks.currency` and `banks.jurisdiction_code` are REQUIRED and carry no
  defaults** (2026-07-31). They previously defaulted to `"GHS"`/`"GH"`
  independently, so they could silently disagree — a bank created with
  `jurisdiction_code="NG"` kept reporting in cedis. Backend code resolves the unit
  through `jurisdictions.base_currency(bank)`, which deliberately has no fallback:
  an unset currency is a skipped decision at the creation site, not a Ghanaian
  bank. Never write a currency literal into bank-facing narrative — the guard suite
  `tests/services/test_jurisdiction_neutrality.py` scans the calculation modules for
  exactly that and is the cheapest place to catch the regression. On the dashboard,
  `fmtCurrency(value)` uses the active jurisdiction; passing a second argument
  OVERRIDES it, which is how eight call sites came to be pinned to `'GHS'` (a
  prettier line-wrap added the literal). Pass the second argument only when the
  currency is genuinely not the bank's own.
  Note there is **no bank-creation path** outside `sample_bank_seed`: ingestion
  requires the bank to exist (`_get_bank_or_404`), so onboarding a non-Ghana
  institution needs that path built, not just these leaks fixed.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
