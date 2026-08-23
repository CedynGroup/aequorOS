# AequorOS Dashboard

The authenticated bank product UI (Next.js App Router). Every figure on every
screen comes from the risk-service API through the generated
`@aequoros/risk-service-api` client — there is no hardcoded financial data in
this package, and no demo fixture module.

**Target deployment:** `bank.aequoros.com`. Public, unauthenticated product
proof lives on the marketing site at `aequoros.com/product#product-ui` as static
UI captures. Do not reintroduce a `demo.aequoros.com` subdomain — it redirected
to login and failed external compliance filters.

---

## Scope

79 App Router page entries across the authenticated shell, login, the inspector,
and dynamic detail routes. The route groups under `app/(app)/`:

| Area | Routes | What it reads |
|---|---|---|
| Overview | `/` | cross-module KPIs, live-engine freshness, filing calendar |
| Liquidity | `/liquidity/*` | LCR, NSFR, monitoring tools, stress, EWI, CFP, submission preview |
| Basel capital | `/basel/*` | RWA, capital structure, CAR, capital stress |
| Interest-rate risk | `/irr/*` | repricing gap, EVE/EaR sensitivity, limits, positions |
| FX | `/fx/*` | net open position, VaR, hedges |
| FTP | `/ftp/*` | transfer curve, product and branch profitability, rates |
| Forecasting | `/forecasting/*` | multi-year projection, scenario, optimizer, what-if |
| Behavioral | `/behavioral` | per-tenant NMD duration, prepayment, deposit stability |
| Markets | `/markets/*` | published curves, indices, desk determinations |
| Positions | `/positions/*` | canonical book and record-level lineage |
| Data Engine | `/data-engine/*` | Excel/CSV, API push, database-direct, T24, market data |
| Submissions | `/submissions/*` | return generation, validation, certification, approvals, artifacts |
| Institution | `/institution/*` | profile, board registers, governance |
| Risk / Alerts | `/risk`, `/alerts` | limit wall, findings, pipeline alerts |
| Reports | `/reports/*` | ALCO, board pack, ICAAP stress pack, comparisons |
| Settings | `/settings/*` | profile, authentication (SSO), signing policy, integration keys |

`app/inspect` is the tenant inspector; `app/api/*` holds the server-only route
handlers for attestation step-up, auth, and impersonation cookies.

### What is deliberately NOT here

- **No mock data layer.** There is no `lib/data/` module. If a screen has
  nothing to show, it renders an empty or "not computable" state rather than a
  placeholder number — see `lib/api/values.ts` (`numOrNull`) and
  `lib/api/fail-open-guard.test.ts`, which fails the build if a hardcoded
  regulatory floor or a zero-on-absence ratio is reintroduced.
- **No second authority for a backend figure.** The browser may format, sum and
  divide what the API returns; it may not decide a number the engines own. See
  "Client-side arithmetic" below for the line between the two.
- **No ML the backend does not have.** The only machine-learning surfaces are
  the cash-flow LSTM (`backend/app/ml`) and the per-tenant behavioral GBMs.
  There is no reinforcement-learning optimizer and no gradient-boosting FX
  ensemble; the forecasting optimizer is a deterministic grid search and the
  balance-sheet forecast is deterministic arithmetic.
- **No filing.** The product generates, validates, certifies and exports
  returns. Transmission to the Bank of Ghana (ORASS) is not implemented — see
  `docs/audit/15_known_limitations.md`.

### Regulatory attribution rules for copy in this package

These are correctness rules, not style preferences. The evidence is
`backend/docs/bog_parameter_sources.md`.

- **CAR** floors are Bank of Ghana (CRD ¶71 minimum plus the ¶75 capital-
  conservation buffer; 13% today). Labelling them `${regShort()} minimum` is
  correct — but only ever as a label on a resolved number. The figure has moved
  four times since 2020 and the ¶71 minimum alone (10%) is **not** the bar a
  bank is measured against, so a `10` written into display code understates it
  while looking authoritative. It is tenant data, never a literal: see rule 1
  under "Client-side arithmetic".
- **LCR and NSFR** are **Basel** standards. The Bank of Ghana has published no
  LCR requirement and nothing at all on NSFR, so any 100% threshold, run-off
  rate, inflow rate or inflow cap in use is a BCBS 238 default. Label these
  "Basel minimum" — never `${regShort()} minimum`, and never "CRD" (the CRD is
  the *Capital* Requirements Directive and contains none of them).
- **CET1, Tier 1 and the leverage ratio** are Bank of Ghana CRD requirements —
  6.5% (¶73(a)), 8.0% (¶73(b)) and 6% (¶90), all VERIFIED with locators. The
  Tier 1 8% coincides numerically with Basel's *total capital* minimum; that is
  a coincidence, and the 8% a bank is shown is BoG's Tier 1 figure, not the
  Basel one. **But do not label these `${regShort()} minimum` on screen.** The
  control plane seeds no governed row for any of the three (only `car_min`), so
  the number on the payload is the institution's own board register value,
  unclamped — the fixture ships `leverage_min = 3`, which is Basel III's figure
  and *below* BoG's ¶90 6%. "Regulatory minimum" is the honest label until a
  governed row exists to clamp against; see `15_known_limitations.md`.
- **IRRBB** shocks are Basel (BCBS d368/d578). Ghana's IRRBB guideline is a
  February 2026 exposure draft stated effective 1 January 2027.
- **LMTD, LRMD, the stress-testing directive and the ICAAP guideline** are all
  February 2026 **exposure drafts**. Copy must say "draft" / "not yet in force";
  it must not call their minimums binding.
- Jurisdiction is data: use `lib/format.ts` (`fmtCurrency`, `regShort()`,
  `centralBankName()`), never a `'GHS'` / `'BoG'` literal in display code.

### Client-side arithmetic

The master directive is that a metric has ONE authority. This package may do
presentation arithmetic over API figures — format them, sum them, take a delta,
divide two sums for a chart. It may not become a second authority for a figure an
engine owns, and it may not invent a threshold.

Three rules, all enforced by `lib/api/fail-open-guard.test.ts`:

1. **A regulatory threshold is resolved, never written down.** Read the floor
   from the payload — the run's §59(f) coupling, `buffers.car_min_pct`, or the
   SDI s.29 summary — and compare with `assessAgainstFloor`. A floor that does
   not resolve is `assessed: false`: render "not assessed", draw no reference
   line, claim neither a breach nor a pass. `numOrNull` + `assessAgainstFloor` +
   `floorStatus` + `fmtFloorPct` in `lib/api/values.ts` are the only mechanism;
   do not add a parallel one. The one sanctioned literal is the Basel 100%
   LCR/NSFR reference, which must be labelled "Basel minimum" (see the
   attribution rules above) — never a CAR floor, which is tenant data.

   **The guard has two spellings to police, and so do you (NEW-51).** The wire
   is snake_case (`car_min_pct`) but almost everything in this package consumes
   the *generated* client, which maps every field to camelCase
   (`json["car_min_pct"] → carMinPct`). The P0-19 rule originally matched only
   the snake_case half, so `num(data?.buffers.carMinPct ?? '10')` sat on the
   Basel overview and the capital planner through the whole remediation
   programme while the suite reported the regulatory UI clean. The rule now
   covers both, plus the no-suffix shape an "assumed minimum" takes
   (`tier1Min ?? 8`) — because an assumed floor still places the breach zone,
   colours the bar and prints a headroom figure against a number nobody set.
   When you touch a threshold, check the field name you actually wrote against
   the rule, not the field name in the OpenAPI schema.

   There is also no "harmless" version of this: the negative control that
   proves the rule uses `?? '13'`, the *correct* Ghanaian figure today, and it
   must still fail. What is wrong is the writing-down — a literal cannot track
   a floor that changes, and it cannot know which tenant it is rendering.

   A floor that legitimately has no source is stated as absent. Never
   substitute a plausible ladder: `10 / 10.5 / 9` looked like a supervisory
   ladder and only the first corresponded to anything published at all.

   `components/basel/FloorNotAssessed.tsx` is the display half of
   `assessed: false` for a limit row. `LimitBar` cannot express "no floor" — it
   needs a number to place the zones and the headroom readout — so branch to
   `FloorNotAssessed` rather than feeding it a stand-in. Charts take
   `number | null` for their floor props and omit the reference line.

   **A floor has ONE authority, and every panel on the page reads it (NEW-53).**
   Resolving the floor correctly in one place is not enough: a page typically
   states the same comparison three times — a KPI status edge, a limit row, and
   a validation rule — and each must derive from the same resolved number, so
   they cannot disagree. The authority is the **governed parameter set on the
   module payload** (`buffers.*MinPct`, the SDI s.29 summary, the run's §59(f)
   coupling), which the backend resolves from the institution's register,
   clamped tighten-only against the control plane, and refuses with 409
   `missing_parameter` rather than guessing.

   **A stored run's `threshold_min` is NOT that authority.** It records what
   was applied when that run executed — evidence about a filing, not the
   requirement in force. Critically, it is absent for every bank before its
   first official run, and "no run" is not "no floor". `/basel` read
   `runMetricThreshold(run, …)` for Tier 1, CET1 and leverage and so rendered,
   on one screen: a green Tier 1 KPI, "This run carries no Tier 1 minimum ·
   NOT ASSESSED", and "PASS — at or above the 8% regulatory minimum". The
   `NEW-53` rule in `fail-open-guard.test.ts` now fails any call to that helper
   inside the regulatory UI; a genuine "what did this run apply" surface adds
   itself to that rule's `allow` map with its reason.

   The corollary is that **`assessed: false` must propagate to every panel, not
   just the limit row.** The KPI takes `floorStatus` (never `ok`); the limit row
   takes `FloorNotAssessed`; and a validation whose floor no longer resolves is
   re-rendered as `assessed: false` on `ValidationItem` — a "Not assessed" pill,
   never a stale pass. Do not resolve a disagreement by hiding a panel: the
   point is that all three agree because all three read one number.
2. **A ratio with no denominator is not zero.** Type it `number | null`, return
   `null`, and render the absence. A guarded division falling back to `0`
   produces a measurement nobody measured: it is indistinguishable on screen
   from a real figure, it plots as a real point, and it compares below every
   floor and above every zeroed floor.
3. **A grouping the backend does not publish is presentational, and must say
   so on screen.** `components/ftp/businessLines.ts` is the worked example: the
   FTP engine prices products and reports branches but has no business-line
   dimension, so the line-level margin has no engine figure behind it. It is
   allowed because it is only summing and dividing engine outputs — and it is
   labelled on the page (grouping rule, margin provenance, and a "view
   aggregate" marker on every surface that carries it) so it cannot be read as
   a priced or reportable FTP margin. If a client aggregate cannot be labelled
   that way, it does not belong in this package.

---

## Stack

- Next.js 14 (App Router) · TypeScript · Tailwind CSS
- TanStack Query over the generated OpenAPI client
- Recharts for visualization
- NextAuth for password and OIDC SSO sign-in
- Inter (UI) + IBM Plex Mono (numerical data) via `next/font/google`

## Run locally

```bash
pnpm install
pnpm --filter @aequoros/dashboard dev
# http://localhost:3001  (marketing site runs on 3000)
```

The backend API must be running (`cd backend && fastapi dev app/main.py --port 8003`).

## Validate

```bash
pnpm --filter @aequoros/dashboard typecheck   # tsc --noEmit
pnpm --filter @aequoros/dashboard lint        # next lint
pnpm --filter @aequoros/dashboard test        # pure-function suites
pnpm --filter @aequoros/dashboard build       # next build
pnpm --filter @aequoros/dashboard e2e         # Playwright (needs object storage)
```

Regenerate the API client after any backend contract change:
`mise run risk-service:openapi-client`.

## End-to-end (Playwright)

`playwright.config.ts` boots a **disposable** stack: the FastAPI backend on a
throwaway sqlite file (deleted and rebuilt every run) plus `next dev` on 3021,
then `e2e/global-setup.ts` seeds through the API and mints per-role session
cookies. It never touches the primary database.

Three prerequisites are not obvious, and each one has already cost a long
diagnosis:

1. **Object storage.** `StorageBackend` is `Literal["s3"]` — there is no
   filesystem mode — so validated packages need a reachable S3/MinIO endpoint.
   Locally it arrives from the untracked `backend/.env` (`S3_ENDPOINT`,
   `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET`); a fresh clone, a git worktree
   or CI has none. `global-setup.ts` refuses to start without it rather than
   letting seven journeys time out one at a time.
2. **Global reference registries.** `scripts/e2e_bootstrap.py` builds the schema
   with `Base.metadata.create_all`, so **no migration runs** — jurisdictions,
   the institution-type registry and the regulatory-parameter control plane are
   all seeded by the bootstrap, from `tests/fixtures/reference_data.py` (the
   same catalogues the migrations read, shared with the hermetic pytest suite).
   Miss one and the stack boots and then fails on the first request that
   resolves a regulatory regime: institution-type resolution is fail-closed by
   design, so an empty registry yields a 409 naming a seed migration that a
   `create_all` database never runs. Adding a new global registry means adding
   it to `reference_data.py`, and nothing else.
3. **The live fact plane.** Every Treasury/ALM cockpit reads
   `current_financial_facts`, which in the product only the background worker
   writes (`pipeline_refresh`), and the e2e stack runs no worker
   (`RUN_INPROCESS_WORKER=0`; `POST /banks/{id}/refresh` merely enqueues a job).
   The bootstrap therefore stands in for the refresh via
   `tests/fixtures/live_plane.py`, mirroring the latest period's facts into the
   live plane and running the product's own `pipeline.recompute_modules`.
   Without it `/basel`, `/liquidity`, `/ftp/*`, `/irr/*` and `/fx/*` all open on
   the "no computed data yet" envelope.

```bash
pnpm --filter @aequoros/dashboard e2e            # the journeys
VISUAL_TOUR=1 npx playwright test visual-tour    # full-page screenshot of every
                                                 # route -> e2e/.tmp/visual-tour/
```

The visual tour is not part of the gate: it exists so a design change can be
reviewed as pixels rather than as a diff. Run it from `backend/dashboard`.

## Deploy to bank.aequoros.com

Separate Vercel/Coolify project from the marketing site.

1. Import the monorepo, root directory `backend/dashboard`.
2. Framework: Next.js. Build: `next build`.
3. Bind `bank.aequoros.com`.
4. `NEXT_PUBLIC_LOGIN_URL` is a **build arg** inlined at compile time — changing
   it needs a rebuild, not a restart.

## Design system

Palette, typography scale, density rules and the traffic-light semantics are in
`docs/DASHBOARD_DESIGN_SYSTEM.md` and `tailwind.config.ts`. Color is functional:
green/amber/red map to regulatory thresholds, never to branding. Numerical cells
are right-aligned with tabular numerals.

**No table may overflow silently (NEW-54).** `DataTable` has always been
`overflow-x-auto`, which scrolls but says nothing, so at 1280px the FTP Line
P&L lost three columns off the right edge of a half-width card and Product
Profitability cut "CONTRIBUTION" mid-word — a column a reader cannot see and
cannot discover is not published. Overflow is now measured (`ResizeObserver` +
scroll position) and, only where it exists, the table gains an edge fade, a
focusable scroll region and a caption naming the gesture; tables that fit are
unchanged. The affordance is the floor, not the goal: when a table overflows
because it was put in a half-width grid cell, move it to a full row — signpost
the overflow you cannot remove, remove the one you can. **Design changes are
reviewed as pixels**: run `VISUAL_TOUR=1 npx playwright test visual-tour` and
compare `e2e/.tmp/visual-tour/` before and after. Type-checking cannot see a
clipped column.
