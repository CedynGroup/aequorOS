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

## Code layout

Feature code moves into `features/<feature>/` one feature per change, with a
boundary ratchet and a move codemod shared with the backend program: see
[FEATURE_LAYOUT.md](FEATURE_LAYOUT.md).

## Scope

App Router pages cover the authenticated shell, login, the inspector,
and dynamic detail routes. The route groups under `app/(app)/`:

| Area               | Routes             | What it reads                                                                               |
| ------------------ | ------------------ | ------------------------------------------------------------------------------------------- |
| Overview           | `/`                | cross-module KPIs, live-engine freshness, filing calendar                                   |
| Liquidity          | `/liquidity/*`     | LCR, NSFR, monitoring tools, stress, EWI, CFP, submission preview                           |
| Basel capital      | `/basel/*`         | RWA, capital structure, CAR, capital stress                                                 |
| Interest-rate risk | `/irr/*`           | repricing gap, EVE/EaR sensitivity, limits, positions                                       |
| FX                 | `/fx/*`            | net open position, VaR, hedges                                                              |
| FTP                | `/ftp/*`           | transfer curve, product and branch profitability, rates                                     |
| Forecasting        | `/forecasting/*`   | multi-year projection, scenario, optimizer, what-if                                         |
| Behavioral         | `/behavioral`      | per-tenant NMD duration, prepayment, deposit stability                                      |
| Markets            | `/markets/*`       | published curves, indices, desk determinations                                              |
| Positions          | `/positions/*`     | canonical book and record-level lineage                                                     |
| Data Engine        | `/data-engine/*`   | Excel/CSV, API push, database-direct, T24, market data                                      |
| Submissions        | `/submissions/*`   | return generation, validation, certification, approvals, artifacts                          |
| Institution        | `/institution/*`   | profile, board registers, governance                                                        |
| Risk / Alerts      | `/risk`, `/alerts` | limit wall, findings, pipeline alerts                                                       |
| Reports            | `/reports/*`       | ALCO, board pack, ICAAP stress pack, comparisons                                            |
| Access control     | `/access/*`        | Members and scoped grants; SSO and access requests; integration keys; My access placeholder |
| Settings           | `/settings/*`      | appearance, profile/account, bank key administration, data and compute status, and About    |

`app/inspect` is the tenant inspector; `app/api/*` holds the server-only route
handlers for attestation step-up, auth, and impersonation cookies.

Capital navigation, planning, and ILAAP access requirements are documented in
[Capital dashboard access](../docs/capital_enforcement_rollout.md#dashboard-access).
FX navigation, dashboard queries, run actions, and report projections are
documented in [FX dashboard access](../docs/fx_enforcement_rollout.md#dashboard-access).
Bank key administration follows [the setup guide](../docs/bank_key_setup.md#bank-administrator-self-service).
Locked fields and editor recovery follow [the protected presentation contract](../docs/bank_field_encryption.md#protected-presentation-reads).

### Unavailable liquidity figures

When LCR or NSFR cannot be computed, its figure and dependent totals show an
unavailable state (**Unavailable** or **—**), and charts leave gaps for missing
readings. The computed sibling remains visible; measured zero and genuinely empty
holdings retain their meaning.
HQLA holdings counts, quality assessments and empty-holdings messages also become
unavailable when the HQLA total is unavailable. The overview liquidity card cannot
claim compliance from a computed sibling or an earlier live result while a current
ratio is unavailable; liquidity is excluded from its assessed-module count.
Risk limits and board-pack figures preserve unavailable values as well.

Detailed refusal reasons and affected-row rendering remain a follow-up for
issue #409. The backend result, filing and operator logging contracts are owned by
[Per-figure results and calculation logs](../../ARCHITECTURE.md#per-figure-results-and-calculation-logs).

### Board pack evidence

The board pack uses the current baseline live generations, regardless of the
reporting-period selector. Headlines and module tables read one live-summary
payload. Each brief prints its producing generation, full input hash, engine
version, source date and UTC computation time. These are live-generation
identifiers, not official-run attestations; registry history never supplies
evidence for these figures. Modules can have different source dates.

Stale generations (including pending recomputation after withdrawal), failed or
blocked computations, and incomplete provenance withhold figures visibly. Missing
modules show an unavailable-evidence notice. Only live-published metrics appear;
validation counts, additional dashboard detail and independently changing alerts
are outside this report basis. The print view carries the same evidence and
refusals. Placeholder engine versions (`live-<module>`) withhold figures with
“version unavailable, recompute to certify” until a new generation supplies its
producing version. FX portfolio VaR carries the producer's exact shared-interval
sample qualification, including the existing insufficient-pilot-evidence warning.
The one-day VaR label does not assume a confidence level.

SDI rating still requires producing-version and input-hash provenance beyond a
pass-through; its incomplete evidence remains withheld. This is follow-up work,
not an alternative version inferred by the board pack.

### Forecasting tools

On **Forecasting → Assumptions**, choose **Propose new version**, enter an
effective-from date, all seven drivers for each preset scenario, and a change
note, then **Save draft**. Use **Edit draft** to revise it and **Submit for
approval** to send it to a checker. The checker can **Approve** or **Reject**;
rejection requires a decision note. The page shows the version in force for the
latest book, the pending change with differences against it, and version history.
The [Forecasting authorization contract](../docs/forecasting_enforcement_rollout.md#dashboard-controls)
owns the view, edit and decision permissions and checker independence. See the
[readiness guidance](../README.md#forecast-assumption-workflow) when no approved
version covers the book date; without a book, the register asks the bank to ingest
one before forecasting can resolve a version.

Balance Sheet, NII, Assumptions, What-if and Optimizer show the approved assumption
version recorded on the run, including its approver, approval time and
effective-from date. The Assumptions preset catalogue also shows its resolved
version; the latest run's values retain their preset, custom-override or
engine-default source labels.

NII reads the saved run selected in its run picker or linked with
`/forecasting/nii?run=<run-id>`, including edited custom runs. After running an
edited scenario, choose **Open NII forecast** in Scenarios to inspect that run.
The page identifies the run and follows its stored horizon for year axes and
labels. Without a selection it reads the latest succeeded base run, falling back
to adverse then severely adverse. Scenario comparisons include the latest
succeeded preset runs and the selected run when distinct; missing comparison
years display no value.

The What-if Lab describes each shock's fixed adjustments to the base assumptions.
These are illustrative projections, not calibrated macroeconomic models. The
optimizer's method note describes its deterministic, exhaustive grid search and
shows the constraint floors from a saved candidate when available. A failed
optimizer run displays its diagnostic, including after reload; it is distinct
from a completed search with no feasible strategies.

Reverse Stress searches the selected reporting period's own financial facts;
it requires those facts and the institution's configured liquidity and capital
stress scenarios. A failed search displays its diagnostic while keeping an
existing frontier visible, or the empty state if no frontier has been saved.
The frontier chart's range comes from the saved search limit for both axes,
including axes that breached before reaching that limit.

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
  the cash-flow LSTM (`backend/app/forecasting/ml`) and the per-tenant behavioral GBMs.
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
- **LCR and NSFR:** label the ratios "Basel reference" and the bank's resolved
  thresholds "governed monitoring thresholds"; a governed value can differ from
  the Basel reference. Use those labels for headroom, trend counts and validation
  titles/messages, never `${regShort()} minimum` or "regulatory minimum".
  Historical dashboard, preview and run reads correct the disclosure without
  changing stored validations, calculations or input hashes; see
  `backend/tests/liquidity/service/test_liquidity_validation_disclosures.py`.
  The [research dossier](../../docs/research/bog_returns_and_templates.md#12-gap-list--what-must-be-simulatedapproximated-clearly-labelled)
  owns the instruments' publication status and calibration gaps.
- **CET1, Tier 1 and the leverage ratio** are Bank of Ghana CRD requirements —
  6.5% (¶73(a)), 8.0% (¶73(b)) and 6% (¶90), all VERIFIED with locators. The
  Tier 1 8% coincides numerically with Basel's _total capital_ minimum; that is
  a coincidence, and the 8% a bank is shown is BoG's Tier 1 figure, not the
  Basel one. **But do not label these `${regShort()} minimum` on screen.** The
  control plane seeds no governed row for any of the three (only `car_min`), so
  the number on the payload is the institution's own board register value,
  unclamped — the fixture ships `leverage_min = 3`, which is Basel III's figure
  and _below_ BoG's ¶90 6%. "Regulatory minimum" is the honest label until a
  governed row exists to clamp against; see `15_known_limitations.md`.
- **IRRBB** uses Basel shocks (BCBS d368/d578) for the supervisory outlier
  test. The overview reports the number of shocks returned by the engine;
  jurisdiction-calibrated parallel ±450bp shocks appear with readable labels
  and informational descriptions when the active parameter set supplies them.
  These additional shocks do not enter the worst-case or breach calculation.
  Instrument status and preparation labels follow the
  [reporting-date contract](../../docs/regulatory_reporting.md#5b-reporting-date-standing-rules).
- **Draft instruments:** copy must say "draft" / "not yet in force" and must
  not call their minimums binding. The
  [research dossier](../../docs/research/bog_returns_and_templates.md) owns publication
  status and conditional commencement; the reporting-date contract governs preparation.
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
   the _generated_ client, which maps every field to camelCase
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
   proves the rule uses `?? '13'`, the _correct_ Ghanaian figure today, and it
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
   clamped tighten-only against the control plane. For calculation refusal
   contracts, see [Per-figure results and calculation logs](../../ARCHITECTURE.md#per-figure-results-and-calculation-logs).

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

- Next.js 16 (App Router, Turbopack) · React 19 · TypeScript · Tailwind CSS
- TanStack Query over the generated OpenAPI client
- Recharts for visualization
- NextAuth for password and OIDC SSO sign-in
- Inter (UI) + IBM Plex Mono (numerical data) via `next/font/local`

### Next.js 16 runtime conventions

- The authenticated route gate lives in `proxy.ts`, which is the Next.js 16
  replacement for `middleware.ts`. It retains the same NextAuth session and
  impersonation-cookie decisions in the Node.js runtime.
- Request-bound APIs are asynchronous. Await `cookies()`, `headers()`, route
  `params`, and `searchParams`; client pages may unwrap promised route props
  with React `use`.
- Framework caching is not an authority layer. The login SSO probe remains
  request-rendered with `cache: "no-store"`, while product data keeps using the
  explicit TanStack Query policy documented below.
- Production builds use Turbopack's per-route client manifests. The Command
  Center bundle guard (`scripts/assert-home-route-bundle.mjs`) requires Recharts
  and the ICAAP ProseMirror editor to stay out of the initial route graph, and
  verifies their runtime markers in the deferred chart and section-editor chunks.

## Query cache and refresh policy

`lib/api/queryPolicy.ts` is the authority for Command Center and regulatory-
dashboard caching. Its keys are prefix-first, with the authority partition
defined in the [authorization foundation](../docs/authorization_foundation.md#effective-authority-projection-and-institution-coverage),
followed by semantic dimensions. Current
and explicit-period dashboard reads have distinct stable keys. The root
`QueryClient` is also replaced and its old cache cleared when the tenant or
authority changes, including entry to or exit from staff inspection; ordinary
access-token rotation deliberately keeps the same cache.

Outside [protected-data recovery](../docs/bank_field_encryption.md#protected-presentation-reads),
heavy liquidity, capital, IRRBB, FX, FTP, regulatory calendar, and live-snapshot
payloads do not use fixed polling. They refresh on mount/window focus, after a
relevant accepted mutation or completed pipeline job, and when the cheap
live-summary generation or freshness official-run signal changes for that
module. Live summary, freshness, and alerts retain a 20-second cadence with
stable ±10% cache-scope jitter; notifications retain 60 seconds with the same
jitter. A failed initial signal
does not block an otherwise available detail response. These signal requests are
observational: in particular, an unchanged live-summary poll performs no enqueue
and no commit. Recalculation comes from accepted input/governance mutations, a
requested refresh, or the worker's due retry of a genuine module exception.

The Command Center ratio panel first shares the current liquidity and capital
queries with the other home consumers. It issues an explicit-period request only
when the current response names a different period (or fails), preserving
historical semantics without duplicating equivalent payloads. The deterministic
fixture records the bounded-idle improvement as **23 resources / 44 calls / 21
detail calls before** and **22 / 33 / 5 after**, with zero detail polling. Keep
`lib/api/queryPolicy.test.ts` and `lib/api/queryAuthorityBoundary.test.tsx` green
when changing query keys, refresh behavior, session authority, or home requests.

The regulatory calendar uses 25-row server pages through the native `DataTable`.
The server computes the complete due-date-ordered horizon and its KPI summary in
one pass before slicing, so paging never changes the headline overdue, due-soon,
on-track, or pending-re-upload counts. Five-sample local Playwright profiling on
the synthetic fixture measured **236 rows / 1,652 data cells before and 25 / 175
after** at the default three-month horizon, and **644 / 4,508 before and 25 / 175
after** at twelve months. The roughly 200 ms long task reproduced in all five
twelve-month baseline samples and in none of the five paginated samples; neither
horizon produced a post-change long task. Keep the native table/column semantics,
row focus, Tab traversal, Enter activation, and labelled keyboard pager when
changing this surface.

Because that panel is below the fold, `DeferredRatioTrendChart` keeps its
Recharts implementation out of the Command Center's initial JavaScript graph.
It preserves the panel's fixed geometry with a loading frame, then requests the
chart when the panel enters a 320 px viewport margin (immediately only when
`IntersectionObserver` is unavailable). Other chart-heavy routes are not
deferred by this boundary. The production `build` command runs
`scripts/assert-home-route-bundle.mjs` after `next build`; the guard fails if a
Recharts chunk returns to the home entry graph or the deferred chart chunk can
no longer be identified.

## Run locally

```bash
pnpm install
pnpm --filter @aequoros/dashboard dev
# http://localhost:3001  (marketing site runs on 3000)
```

The backend API must be running (`cd backend && fastapi dev app/main.py --port 8003`).

Configure authentication using [.env.example](.env.example), including its
production-only `AUTH_URL` guidance. Use one browser host consistently:
`localhost` and `127.0.0.1` have separate cookie jars. In development,
the route proxy warns once when `AUTH_URL` (or legacy `NEXTAUTH_URL`) names a
different host from the request.

### Sign-in and session recovery

An unreadable session, including one encrypted with another secret, is treated
as signed out. Login and protected-page redirects expire known Auth.js and
NextAuth session-cookie variants, including secure/host-prefixed names and
received numeric chunks. The auth response boundary also clears these variants
for an empty session or successful sign-out, and removes stale siblings when
issuing a session. Sign-out still invokes backend refresh-token revocation.
See `lib/authCookies.ts` and the [browser coverage guidance](#end-to-end-playwright).

Login, sign-out, and session redirects retain the request's origin; a callback
on another origin returns to the current origin instead. In the dashboard,
`NEXT_PUBLIC_LOGIN_URL` selects only the path, query, and fragment (default
`/login`), even if configured as an absolute URL. Production origin detection
uses the trusted proxy's forwarded host and protocol; local development prefers
the browser's Host header.

The backend-unreachable sign-in message means a backend network or HTTP 5xx
failure. Rejected credentials retain the invalid-email-or-password message;
other authentication errors show a generic sign-in failure.

## Validate

```bash
pnpm --filter @aequoros/dashboard typecheck   # tsc --noEmit
pnpm --filter @aequoros/dashboard lint        # ESLint CLI
pnpm --filter @aequoros/dashboard test        # legacy + Vitest behavior suites
pnpm --filter @aequoros/dashboard test:coverage # CI coverage and shrink-only ratchet
pnpm --filter @aequoros/dashboard build       # next build + home bundle guard
pnpm --filter @aequoros/dashboard e2e         # Playwright; package journeys need S3/MinIO
pnpm --filter @aequoros/dashboard e2e:contracts # browser presentation and HTTP contracts
```

Regenerate the API client after any backend contract change:
`mise run risk-service:openapi-client`.

### Unit-test collection

Legacy Node assertion suites and native Vitest behavior suites both run through
`test`. `tsconfig.test.json`
compiles every `*.test.ts` and `*.test.tsx` file; its explicit production roots
support existing tests that load modules dynamically. `scripts/run-unit-tests.mjs`
discovers all source test files, fails below the 55-file floor or when any source
test lacks compiled output, and executes each file in its own Node process.
There is no hand-maintained execution list. New nested TS and TSX tests are
included automatically. Once the collection runner starts, CI uploads the
discovered, executed and failed file names in `dashboard-unit-collection`,
including partial evidence if execution fails.
The script contract tests also run through the standard `test` command.

New Vitest behavior tests live in `unit/**/*.spec.ts(x)`; `vitest.config.mjs`
collects that entire directory without a file list and excludes Playwright specs.
Follow the [shared testing standard](../../CODEBASE_CONVENTIONS.md#behavior-properties-and-state-machines-backend-and-dashboard):
Testing Library queries by role, label and text, API-boundary mocks only, and
fast-check for broad formatting and numeric input spaces. Legacy suites retain
isolated processes because several replace Node's module loader.

Interactive specs opt into jsdom with `@vitest-environment jsdom`, use
`user-event` and the real React Query/authority providers, and intercept HTTP
with MSW's Node server. Treat the generated client and feature hooks as part of
the behavior under test. Reject unhandled requests, reset handlers and clear
query/token state after each test. No browser service-worker build is needed.
The liquidity specs cover stored decimal ladders, sensitivity/class grants and
cash-flow date submission, currency totals, refusals and bank/authority cache
isolation; their properties use 100 runs with fast-check's failure seed/path.
Capital/Basel specs exercise stored scenario selection, missing versus zero ratios,
percentage-point headroom, governed-floor disclosures and regime-specific columns.
FX specs cover persisted position/parameter snapshots and scenario figures/verdicts.
Their 100-run properties preserve decimal values and enforce exact sensitivity,
whole-institution capital scope and entitlement boundaries.
Credit specs check saved PD estimates and disclosures, migration entry/departure
amounts, roll-rate absence versus zero, and scoped row versus whole-book grants.
FTP specs check saved pricing assumptions, conservation of business-line view
balances/contributions/flags, uncomputable margins and confidential scenario grants.
These read-only analytical tables do not implement status transitions.

The formatting and chart properties cover compact money rounding/error bounds,
amount ordering, UTC reporting-date round-trips, missing versus measured chart
points, waterfall balance conservation and tenant labels rendered as tooltip text.
Stored ladder properties also check that changing one horizon preserves the other
currencies and stored net/cumulative figures. Filing capability properties generate
module, sensitivity and permission combinations independently for sibling banks.
These pure properties use 100 runs and preserve fast-check's failure seed/path.

`unit/filing-state-machine.spec.tsx` models server status responses and officer,
institution, authority resolution, clearance and in-flight changes through the
public filing projection and visible `PrimaryActionButton`. Every status/officer
checkpoint runs explicitly; generated sequences use 20 examples of at most 25
commands. The UI transition model is:

| Source                             | Server response                    | UI effect                                                                                                |
| ---------------------------------- | ---------------------------------- | -------------------------------------------------------------------------------------------------------- |
| Draft                              | Generated                          | Certification waits for clean validation                                                                 |
| Generated                          | Validated or superseded            | Certification becomes available, or the version closes                                                   |
| Validated                          | Pending approval or superseded     | Preparation disappears; the approver may review, or the version closes                                   |
| Pending approval                   | Approved or rejected               | Approval disappears; the validator may file with clearance, or a preparer may create a corrected version |
| Approved                           | Submitted or superseded            | Only the validator may check the regulator's decision, or the version closes                             |
| Submitted                          | Acknowledged, rejected or declined | The version closes, or correction starts on a new version                                                |
| Rejected                           | Superseded                         | The old version closes                                                                                   |
| Acknowledged, declined, superseded | None                               | No filing action is offered                                                                              |

Attempts to act run at every status, including frozen and terminal checkpoints;
unresolved, examiner and sibling-bank authority must offer no act. Pending requests
disable the offered act. The frontend consumes server statuses; it does not enforce
server transitions, freeze financial content or make the final contextual
maker-checker decision. Those invariants are exercised by the
[backend state machines](../docs/state_machine_testing.md), not inferred from a UI button.

`unit/transmit-confirm-state-machine.spec.tsx` exercises the dialog's own
acknowledgment state with Testing Library: unacknowledged → acknowledged →
pending → refused/retry, plus cancel/reopen. Confirmation requires acknowledgment
and no in-flight submission; network rerenders retain acknowledgment, while a new
dialog requires it afresh. A fixed refusal/retry path complements 20 generated
examples of at most 15 commands. Each example unmounts its components and resets
shared jurisdiction state, and failures retain fast-check's replay seed/path.

BI contract suites use `unit/backend.ts` to execute the backend's actual schema
validators and serializers through `uv run --frozen --no-python-downloads`.
Running `test`, `test:legacy` or `test:coverage` therefore also requires uv and
Python 3.13; prepare the [backend environment](../README.md#local-setup) before
running them. The helper clears database URLs and disables the in-process worker;
these contract checks need no running backend or database.

`test:coverage` runs both engines with V8 coverage. Vitest's AST-based report
includes **every production TS/TSX file in `app/`, `components/` and `lib/`**,
including untouched modules. Only tests and declaration files are excluded.
The guard independently inventories those roots and fails if any file is absent.
c8 collects the legacy child processes and remaps their emitted JavaScript to
original TypeScript through compiler source maps. The combined **line** report
uses Vitest's executable source lines as its denominator and counts a line as
covered when either engine exercised it. Extra c8 lines cannot inflate coverage.
Branch, function and statement reports remain separate by engine; their mapping
schemes differ, so they are not added together. Source-text invariant guards do
not count as execution of the modules they inspect.

Raw c8 V8 data, including embedded source maps, lives in `.test-out/coverage-v8`,
which the existing source guards exclude and the coverage runner cleans on exit.
Final c8 reports stay in `coverage/legacy`; native and combined reports stay under
`coverage/`. These final reports remain available for ordinary test runs and CI
artifacts; raw V8 data is not uploaded.

CI uploads `dashboard-unit-coverage`: native Vitest HTML/LCOV/JSON, legacy JSON,
combined line LCOV and `coverage/combined/summary.json`, plus both collection
reports. Partial reports are retained on failure. The generated combined summary
owns measured line coverage, production-file counts and collection totals;
`unit-coverage-baseline.json` owns the committed uncovered-line ceilings.
The report exposes untested production code rather than reporting only loaded
modules. The guard also rejects incomplete legacy collection, fewer than eight
native tests, or skipped native tests.

`unit-coverage-baseline.json` pins the maximum number of uncovered lines both
in total and per file. These ceilings may only shrink; improvement elsewhere
cannot conceal a file's regression. A new production file has a zero-uncovered
ceiling. After improving tests, run `pnpm --filter @aequoros/dashboard coverage:ratchet`
to rerun both engines and tighten the baseline, then review and commit it.
That command refuses increases rather than accepting a new lower standard.
Deleting unused production code can also shrink the baseline; intentional
changes to the coverage policy require explicit review. Include measured
coverage before and after in each behavior-test PR.

## End-to-end (Playwright)

`playwright.config.ts` boots a **disposable** stack: the FastAPI backend on a
throwaway sqlite file (deleted and rebuilt every run), a local OpenID Connect
issuer standing in for the tenant's identity provider, and `next dev`. All
three servers use OS-selected free ports (optional `E2E_BACKEND_PORT`,
`E2E_DASHBOARD_PORT` and `E2E_IDP_PORT` preferences fall back when occupied),
then `e2e/global-setup.ts` seeds through the API and mints per-role session
cookies. The Next.js server uses `.next-e2e`, so it cannot contaminate the
normal development cache. Both browser suites use the worktree's disposable
database and session files, so a worktree-local lock rejects concurrent E2E runs.
The stack never touches the primary database.

The layout and login fonts are bundled as Latin-only WOFF2 files in
`app/fonts` (sources and OFL licenses live alongside them). Production builds,
journeys and the visual tour use the same local fonts without fetching Google
CSS or font files.

The attestation workspace journey checks every offered typed face in both the
adoption panel and the placed signature preview. It verifies the fonts Chromium
actually renders and hashes the downloaded script fonts against the backend's
bundled files (`e2e/support/typed-fonts.ts`).

The backend server environment pins `APP_ENV=test` and
`TLS_ALLOW_PLAINTEXT=1` because this disposable stack uses SQLite and loopback
HTTP (including MinIO). Test mode alone does not relax the transport policy;
without the explicit opt-in, startup refuses SQLite with "Database transport
requires PostgreSQL with a hostname." Exception limits are governed by
[the transport contract](../docs/transport_security.md#local-development-and-narrowly-bounded-exceptions).

`e2e/session-cookie-hygiene.spec.ts` deliberately sends a cookie encrypted with a
foreign secret, verifies it is cleared and completes a subsequent real login.
Auth.js reports `JWTSessionError` and "no matching decryption secret" during that
refusal. `contracts/session-http.spec.ts` checks malformed cookie families and
runtime host warnings.
The server configuration imports the minting helper's test secret constants, and
`session-lifecycle.spec.ts` decrypts a real login cookie and verifies rotation.

The credential helpers in `e2e/support/mint.ts` read the principal's current
authorization version from the disposable SQLite fixture on each default mint.
After a real grant, revocation or status change, mint a new token or storage
state instead of counting version increments. A session cookie and its embedded
access token share one captured version. Pass an explicit version only when a
journey deliberately needs a captured or invalid authorization generation;
minting a token does not alter the principal's persisted bindings or status.

Authorization, access-request, baseline-membership, live-grant, grant-administration,
macro-contract and full-filing stories use `e2e/support/story-identities.ts` to
provision fresh tested actors. Declare the required profiles with `storyConfig`;
owner stories also request an isolated setup tenant. Shared-bank stories keep
the bootstrap owner only for fixture administration. The fixture installs each actor's real scoped bindings
and current session before browser creation. Its `finally` cleanup closes story
contexts, rejects their pending requests, archives governed clones and deactivates
the actors, revoking refresh families while retaining audit and signed evidence.
Selected identity aliases and storage-state files are restored even after failure.
The Python helper refuses any database except this worktree's disposable fixture
and provisions no financial facts.

`storyConfig.bindings` gives baseline actors complete, exact-bank grants with
explicit bundles, modules and sensitivities. Credit grants can also name a branch
slice; the journeys compare actual rows with a whole-book reader and check that
client filters cannot widen it. Permission stories pair visible UI restrictions
with direct API refusals using the same actor's credential. An isolated owner
story can set `ownerReadModules` to revoke its initial broad read grant through
the grant service and replace it with module-specific read grants.

Three prerequisites are not obvious, and each one has already cost a long
diagnosis:

1. **Object storage.** `StorageBackend` is `Literal["s3"]` — there is no
   filesystem mode — so validated packages need a reachable S3/MinIO endpoint.
   The standard `pnpm e2e` command provisions a worktree-local MinIO when no
   `S3_ENDPOINT` is exported. Existing exported S3 configuration is reused in
   auto mode; `AQS_LOCAL_SERVICES=external` also permits the backend to read its
   untracked `.env` without starting a service. The `attestation`, `full-lifecycle`,
   `submission-lifecycle`, `data-engine-ingestion`, `integration-key-use`,
   `grant-administration`, `00-ingestion-worker`, `00-treasury-boundaries`, and opt-in `visual-tour`
   specs, plus the SSO attestation journey, refuse immediately without it rather
   than letting storage-dependent assertions time out one at a time.
   Storage-free specs remain runnable without any S3 configuration. The
   GitHub Actions journey workflow starts a disposable MinIO with
   its built-in KMS and runs the standard suite against that real object store.
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
   writes (`pipeline_refresh`), and the shared fixture runs no worker
   (`RUN_INPROCESS_WORKER=0`; `POST /banks/{id}/refresh` merely enqueues a job).
   The bootstrap therefore stands in for the refresh via
   `tests/fixtures/live_plane.py`, mirroring the latest period's facts into the
   live plane and running the product's own `pipeline.recompute_modules`.
   Without it `/basel`, `/liquidity`, `/ftp/*`, `/irr/*` and `/fx/*` all open on
   the "no computed data yet" envelope.
4. **Bank-held keys.** The full suite requires the bank-key emulator described in
   [the encryption journey setup](../docs/bank_encryption.md#browser-encryption-journeys).
   Start it and export `BANK_KMS_ENDPOINT_URL` before the commands below. The local
   service runner provisions Postgres and MinIO; it does not start this emulator.

`00-ingestion-worker.spec.ts` exercises the worker path separately. It provisions
an empty tenant and real analyst/approver bindings, uploads a balanced synthetic
book and market data through the bank UI, and approves forecast assumptions
through the bank API. A standalone `app.worker` claims the queued refresh and
makes all eight modules current. The journey pins treasury's GHS 2 million HQLA,
GHS 310,000 net outflows and 645.16% coverage, then uses Mint official run through
the same worker and generates BSD2 with the matching cash amount. It refuses to
start with another bank's queued work, stops its own worker in `finally`, and
removes its tenant and session state. Keep this story ahead of shared-bank
mutation stories and keep Playwright at one worker.

`00-treasury-boundaries.spec.ts` uses the same upload and worker path with a
fresh setup tenant and fresh analyst/approver identities per story. It pins zero
cash, the 100% LCR threshold, the 75% inflow cap, Level 2A/2B haircuts and both
HQLA caps, and exclusion of public-sector securities lacking HQLA classification.
A savings-and-loans variant checks its own capital floor, omits Basel sub-tier
ratios and refuses a bank forecast for its regime. `storyConfig.institutionType` selects the institution
regime only for an owned tenant. Securities declare their issuer class separately
from the bank's HQLA classification. The fixture stops its owned worker before
identity retirement; it neither completes jobs nor writes financial facts itself.

The canonical fixture ends at a fixed month. Bootstrap first carries it forward
(`extend_canonical_test_book`) one month-end snapshot at a time through the last
month end on or before today, each repeating the canonical latest fact set.
After loading the canonical position book, it replaces the latest period's
`credit_exposure` facts and `credit_source_basis` with values derived from those
accepted sources. Enterprise stress checks that exact provenance before calculating:
the aggregate spine's initial empty source basis cannot authorize stress of a
subsequently added position book. Other fact groups and historical periods
retain the canonical spine's figures. Capital and stress expectations for the
latest period therefore use the position book's net CRD credit basis.

```bash
pnpm --filter @aequoros/dashboard e2e            # functional journeys
pnpm --filter @aequoros/dashboard e2e:contracts  # browser presentation and HTTP contracts
AQS_LOCAL_SERVICES=native pnpm --filter @aequoros/dashboard e2e:visual
```

Functional outcomes live in `e2e/`: source ingestion, computed figures,
navigation and recovery, scoped authority, and persisted user actions.
`contracts/` owns presentation and HTTP contracts: header geometry and metadata,
Settings section placement, authentication/cookie protocols, grant/access-request
contracts, BI authorization/privacy/governance, and the exhaustive macro-scenario
API catalogue. Both commands boot the same
disposable product stack with one worker and no retries, on separate invocations.
The contract suite reports every collected test and its final result through
`e2e/support/contract-reporter.ts`, alongside the list reporter. Skips, missing
results and expected failures fail the suite. `PLAYWRIGHT_CONTRACT_REPORT` selects
the JSON destination (by default, `contracts.json` in the test-results directory).
This report is separate from journey accounting. The aggregate gate requires the
exact names in `.github/scripts/dashboard_contracts.json`; removing, renaming or
adding a contract requires a reviewed manifest change, just as the journey floor
does. A passing critical ownership outcome cannot replace other contracts.

Navigation/recovery remains in `e2e/navigation-recovery.spec.ts`; macro read-only
UI checks live together in `e2e/default-macro-scenarios.spec.ts`. Settings authority
and preference persistence use fresh actors, while shell presentation is checked
once in the contract suite.

Treasury risk stories are separated by observable outcome. Each opens its module
afresh; scenario history and board-pack stories execute their own prerequisite
run, so they can be selected without running an earlier story first. The coherent
ingestion-to-worker-to-generated-return journey stays together.

The visual tour is not part of the gate: it exists so a design change can be
reviewed as pixels rather than as a diff. Run the commands from the repository
root; the tour writes full-page screenshots of every route to
`backend/dashboard/e2e/.tmp/visual-tour/`.

The Data Engine journeys (`e2e/data-engine-ingestion.spec.ts`) use the fixtures in
`e2e/fixtures/data-engine/` to cover Excel/CSV upload, legacy `.xls` refusal,
batch findings, correction by re-upload, downstream positions, and manual market
data upload. The machine-key journey (`e2e/integration-key-use.spec.ts`)
provisions its own two-bank tenant with real scoped bindings. A key issued in the browser pushes a
balanced synthetic book through the Data Engine, is refused by the existing
sibling bank, and loses push and batch-read access after browser revocation.
Canonical balances, batch lineage and absence of sibling writes are checked
before finally-based credential and tenant cleanup.

The full filing lifecycle (`e2e/full-lifecycle.spec.ts`) takes both a regulator
rejection and a checker send-back through correction of a synthetic source book
via the Data Engine, a replacement package, renewed maker and checker signatures,
and acknowledgement. It pins the replacement cash figure and its new source run
while preserving the superseded snapshot and hash, withdrawn signatures and
earlier receipt history. A previous version's review basis is refused, repeated
Validator approval is idempotent, and duplicate transmission cannot add a receipt.
Package versions advance independently of the
ORASS revision, which counts regulator-granted resubmissions.

The T24 journey configures a core in the browser, stages and ingests
a recorded OFS extract through the API, then checks its batch and positions in
the browser; live transport remains unavailable. Set `E2E_EVIDENCE_DIR` to retain
their screenshots.

### Local services without Docker or OrbStack

Install the native binaries once on macOS (do not run `brew services start`):

```bash
brew install postgresql@17 homebrew/core/minio
```

PostgreSQL 17 matches CI; PostgreSQL 18 is refused. Native ownership checks use
`lsof` (included on macOS) to confirm the child owns its port before sending
health, storage or database requests; an occupied port fails without provisioning
the existing service. The Homebrew core bottles work even when the MinIO tap's
upstream download URLs have been removed.
On other Unix hosts, install MinIO and `lsof` on `PATH` and point `AQS_POSTGRES_BIN` at
the PostgreSQL 17 `bin` directory.

```bash
# From the repository root, force native services even if Docker is running:
AQS_LOCAL_SERVICES=native mise run risk-service:test-postgres-suite
AQS_LOCAL_SERVICES=native mise run risk-service:test-postgres-schema
AQS_LOCAL_SERVICES=native mise run risk-service:test-postgres-locks
AQS_LOCAL_SERVICES=native mise run risk-service:test-storage
AQS_LOCAL_SERVICES=native pnpm --filter @aequoros/dashboard e2e
```

The default `auto` mode uses Docker when its daemon responds, and falls back
to native binaries when Docker is absent, stopped, or unreachable. Use
`AQS_LOCAL_SERVICES=docker` to require Docker (Compose 2.24.4+), or `external`
to use only your supplied environment. CI and exported `TEST_DATABASE_URL` /
`S3_ENDPOINT` bypass automatic provisioning, preserving the existing CI setup.
An explicit native or Docker mode overrides those URLs with local ones.

`backend/scripts/local_services.py` selects free loopback ports and stores
native Postgres/MinIO data, the KMS key, state and logs in the gitignored
`.local-services/` directory at this worktree's root. It never starts or stops
the default Homebrew cluster. It creates the `risk_service` database with a
non-superuser, non-BYPASSRLS test role (CREATEROLE only for schema/migration
tests), and a private `risk-local` bucket with MinIO's built-in `aequoros-key`
KMS enabled. No bank data is seeded by the service runner. Docker mode uses
the existing pinned compose images with a worktree-specific project and ports.
Startup verifies an encrypted object write/read/delete, so a full drive or
broken KMS fails before the suite starts. Native E2E runs disable Next's
persistent development compiler cache to conserve the host disk shared with
MinIO; ordinary development, Docker runs and CI keep their existing defaults.

Test commands stop the services they use on exit, failure or interruption; data
is retained for the next run. Only one managed service command runs per
worktree at a time, so run suites sequentially. Other worktrees have separate
clusters, storage, locks and ports. To reclaim disk space while no test command
is running, remove this worktree's `.local-services/` directory. This discards
only local test data.
If MinIO reports insufficient free disk space, also remove the disposable
`backend/dashboard/.next-e2e` cache after stopping its E2E dev server.

### Single sign-on against a local issuer

The SSO journeys (`e2e/sso-sign-in.spec.ts`) need no bank IdP or additional
identity-provider setup. With the [stack prerequisites](#end-to-end-playwright)
in place, run `pnpm --filter @aequoros/dashboard e2e`; the SSO attestation
journey also requires object storage. The config
starts `backend/scripts/e2e_idp.py` — a small, spec-shaped OpenID Provider
(discovery, a real credential form, PKCE, `client_secret_basic` and
`client_secret_post`, RS256 JWKS, `auth_time`) bound on loopback — and the
bootstrap registers it as the e2e tenant's `sso_connections` row through the
same `app.identity.service.sso_config` path the dashboard's
[Access authentication panel](../../docs/rbac.md#102-access-control-access-visible-to-every-active-member) uses, with
`SSO_INTERNAL_KEY` set on both servers so the dashboard's server-to-server
client-config read is live. A plain-http issuer is accepted only because the
backend and the dashboard egress guards both carve out loopback on an
undeployed `APP_ENV`; neither guard is weakened, and the same issuer is refused
on every deployed environment by construction.

Three static accounts, one password (`e2e-idp-password-not-production-000`),
each a literal in the repo because none of them may ever be real:

| Account                                  | Tenant outcome                                                                                                  |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `e2e.sso_analyst@aequoros.example`       | Pre-provisioned officer with Analyst authority — links by email on first sign-in and step-up signs as preparer. |
| `e2e.sso_unprovisioned@aequoros.example` | Allowed domain, no account — refused on the login page, no session.                                             |
| `e2e.sso_outsider@contractor.example`    | Outside the connection's allowed domains — refused the same way.                                                |

The journeys prove the login page's SSO button → issuer → NextAuth callback →
`/auth/sso` exchange → session, sign-out and a second sign-in from a clean
slate, and the attestation step-up round trip (`prompt=login`, `max_age=0`,
the signer returned to the same ceremony with the markers consumed and the
authorisation spent server-side) — plus its inverse, the honest "not
configured" return when the connection is switched off through the
account-administration API mid-ceremony. Set `E2E_EVIDENCE_DIR` to keep
screenshots of the issuer form, the signed-in shell, the refusals and the
step-up return. To drive the issuer by hand:

```bash
cd backend && E2E_IDP_PORT=8120 uv run python scripts/e2e_idp.py
```

The `Dashboard Playwright journeys` workflow's automatic triggers are governed by
[CI enforcement](../../ARCHITECTURE.md#ci-enforcement). It also remains available through the Actions UI or
`gh workflow run dashboard-journeys.yml --ref <branch-or-commit>`.
Journeys run for every change except PRs whose entire diff is on this ignore list:

- Root Markdown files and `LICENSE`.
- `docs/` and `backend/docs/` documentation and their images.
- `README.md` directly under `backend/`, `backend/dashboard/`, `console/`,
  `frontend/` or `deploy/`.
- Marketing source and images under `frontend/app/`, `frontend/components/`,
  `frontend/lib/` and `frontend/public/`.

These paths are not consumed by the journey stack. Other files, including every
workspace manifest, lockfile, interpreter file, workflow, action and script,
select the suite. Dashboard images and runtime Markdown also select it.
The shared detector's ignore mode is used only by this workflow; other workflows
keep their inclusion predicates. Renames consider both the old and new path.
All events other than pull requests run every shard; unknown or empty change
decisions fail closed. The **`Dashboard journeys gate`** is the stable check to
require before merge: it always reports and accepts a skipped suite only after
successful detection of an ignored-only PR.

Four runners use Playwright `--shard=1/4` through `--shard=4/4`, each with one
worker and its own disposable SQLite database, fixture bank, loopback MinIO
with built-in KMS and local OIDC issuer. Shard 1 then runs the browser contracts
serially on a fresh database with a separate results directory. A contract
failure fails that shard job and the required gate even when every journey
report meets the numeric floor. Contracts never contribute to that floor.
Tests in one spec stay together; no mutable bank or storage is shared between shards. Each runner restores or
installs Chromium and installs its system dependencies. Evidence screenshots
are uploaded as `dashboard-journey-evidence-<shard>` and machine-readable
reports as `dashboard-journey-report-<shard>`; the gate summarizes the combined
counts. Complete Playwright output directories, including failure traces, worker
logs and screenshots, are uploaded even after failure as
`dashboard-journey-results-<shard>` and `dashboard-browser-contract-results`.
`e2e/support/artifact-reporter.ts` persists inline attachment bodies (including
screenshots) inside those output directories; list and outcome reporters alone
keep those bytes only in memory. Separate output directories preserve journey
attachments when contracts start.
The full contract inventory is uploaded as `dashboard-browser-contract-report`.
The aggregate requires all four reports, unique journey identities,
and the executed-journey floor defined in the
[gate script](../../.github/scripts/assert_dashboard_journeys.mjs), with zero skips. It verifies the
reported counters against the named results and prints every skipped journey
in the log and job summary before failing. Retries replace the same journey's
result and cannot inflate the count. Missing/unfinished tests or a lost shard
fail the gate even when the remaining reports pass. Raise the floor when adding
coverage; reducing it requires a reviewed suite change.

The [critical-outcome catalogue](../../.github/scripts/dashboard_critical_outcomes.json)
beside the numeric gate names a small set of required business outcomes. Each
owning test declares its stable ID as a `critical-outcome` Playwright annotation.
The outcome reporter records actual final results separately for journeys and
contracts. The aggregate requires all four journey reports and the unsharded
contract report, and rejects missing, duplicate, unknown, failed or wrongly
layered outcomes even when the numeric floor passes. Keep IDs stable when
renaming or splitting stories; changes to the catalogue require review. These
IDs guard scenario presence and passing execution; meaningful assertions and
code review still determine whether each story proves its declared outcome.

Screenshot diagnostics are separate from the required suite; see the visual-tour
capture command and output under [End-to-end (Playwright)](#end-to-end-playwright).

Each shard also requires at least 20 executed journeys and the quarantine in
`e2e/support/quarantine.ts` to match the size the workflow pins. A journey may only be parked there by name,
declared with `test.fail` and a reason beside it, and with both the workflow's
shard gate and `.github/scripts/assert_dashboard_journeys.mjs` edited to admit
it. The aggregate currently refuses any quarantine. The custom reporter fails
the run for any non-quarantined failure, unexpected quarantine pass, skipped or
undiscovered quarantine entry,
or `test.fail` declaration missing from that list.

Reporting-date selection and exact snapshot matching are governed by
[the reporting-date contract](../../docs/regulatory_reporting.md#5a-reporting-date-vs-data-arrival-corrected-2026-08-23).
The fixture bootstrap described under [End-to-end (Playwright)](#end-to-end-playwright)
supplies the snapshots those package-generation journeys require.

## Deployment health

The aequorOS dashboard exposes `GET /api/health/live` without authentication or
redirects. It returns HTTP 200 with
`{"service":"aequoros-dashboard","status":"ok"}` and
`Cache-Control: no-store`. This checks the Next.js server process; backend,
database and SSO availability are independent. No credentials or tenant data
are read, and no session cookies are set.

The existing container healthcheck calls
`https://dashboard:3000/api/health/live` with the existing service trust CA,
rejects redirects, and bounds the request to four seconds. The external
deployment monitor should call
`https://bank.aequoros.com/api/health/live` without cookies or bearer tokens:

```bash
curl --fail-with-body --max-time 6 --include https://bank.aequoros.com/api/health/live
```

For installations using `NEXT_PUBLIC_BASE_PATH`, prepend that configured path
to the external health URL and update the container healthcheck URL to match;
the compose wrapper uses the unprefixed path. The exact health path bypasses
the auth proxy; other health paths remain protected. Use the backend's separate
[`/api/health/live` and `/api/health/ready`](../README.md#deployment-health)
for API process and dependency monitoring.

## Deploy to bank.aequoros.com

Use [the repository-root compose wrapper](../../docker-compose.dashboard.yml)
as a separate Coolify app from the marketing site. Its header owns the build
pack, base-directory, domain, and port settings. Complete
[the transport deployment preparation](../docs/transport_security.md#deployment-preparation-review-before-applying)
before applying the template.

`NEXT_PUBLIC_LOGIN_URL` is a **build arg** inlined at compile time — changing
it needs a rebuild, not a restart.

When changing the public host, update the dashboard's `AUTH_URL`, backend
`CORS_ORIGINS`, and marketing app's `NEXT_PUBLIC_LOGIN_URL` destination, and
re-register OIDC redirect URIs with each bank's IdP. The marketing login URL is
also a build argument and requires a rebuild. Dashboard login-path semantics
are described under [Sign-in and session recovery](#sign-in-and-session-recovery).

## Design system

Palette, typography scale, density rules and the traffic-light semantics are in
`docs/DASHBOARD_DESIGN_SYSTEM.md` and the `@theme` block in `app/globals.css`.
Color is functional: green/amber/red map to regulatory thresholds, never to
branding. Numerical cells are right-aligned with tabular numerals.

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
reviewed as pixels**: follow the visual-tour capture instructions under
[End-to-end (Playwright)](#end-to-end-playwright) and compare the screenshots
before and after. Type-checking cannot see a clipped column.

### Encryption journeys with local KMS

Follow [the browser encryption journey guide](../docs/bank_encryption.md#browser-encryption-journeys)
for emulator setup, the focused command, fixture isolation, recovery coverage and
evidence paths. Its [emulator contract](../docs/bank_encryption.md#local-kms-emulator-and-ci)
owns endpoint restrictions and the cross-account-grant limitation.

### Contingency funding plan lifecycle

Liquidity → CFP authors each of the seven required plan-content blocks and
optional behavioral overlays. Every command requires a reason; Board approval
also requires an approval reference and an independent checker. The server
remains authoritative for the complete Liquidity/Confidential binding and
maker-checker decision. Readers see disabled controls with permission reasons.

Save any content changes before recording Board approval. Saving makes you the
preparer, so an independent checker must review the saved draft. Approval is
bound to the revision the checker loaded: if another session saves a revision,
the server refuses approval with HTTP 409. Use **Reload latest plan**, review
the revised contents, then record approval again.

| Source             | Command               | Authority and guard                                    | Result                                             |
| ------------------ | --------------------- | ------------------------------------------------------ | -------------------------------------------------- |
| No plan / approved | Save draft            | Create                                                 | New draft version; previous approval retained      |
| Draft              | Save draft            | Edit                                                   | Draft revised; preparer updated                    |
| Draft              | Record Board approval | Approve, independent of preparer, complete contents    | Approved version; prior approval superseded        |
| Approved, inactive | Activate              | Approve, unexpired approval, selected reporting period | Active; append-only event and indicator snapshot   |
| Approved, active   | Stand down            | Approve, selected reporting period, reason             | Inactive; append-only event and indicator snapshot |

Activation without approval, expired approval and duplicate activation are
refused. Stand-down without an active plan is refused; an active plan can still
be stood down after its annual approval expires. Each accepted command reloads
plan, event and indicator reads. Activation and stand-down produce internal
regulator-notification records; aequorOS does not transmit them externally.
Draft and approval audit records remain in the audit trail; the activation log
contains activation and stand-down events. This workflow does not create or
replace official regulatory runs. Related journeys and guards: #289, #315, #323.

### Treasury scenario workspaces

Each treasury module's **Scenarios** tab contains its own effective-dated
scenario definitions and latest stored results for the selected reporting period:
IRRBB `/irr/scenarios`, Liquidity `/liquidity/stress`, FX `/fx/scenarios`, FTP
`/ftp/scenarios`, and Capital `/basel/stress`. Opening a tab creates no runs.
Liquidity scenario definitions require Liquidity confidential view; they no
longer require the unrelated Risk & Limits view. Definitions require the owning
module's confidential view with whole-institution coverage; result summaries
retain the server's existing sensitivity-specific projection.

The enterprise stress workbench remains at `/irr/enterprise-stress`, with its
own IRRBB tab and links from the module workspaces. It runs a governed macro
scenario across engines. Module scenario tabs never dispatch enterprise stress.
Its IRRBB correctness dependency remains #306.

FTP what-if compares baseline with a chosen module scenario and optional curve
shift, funding-spread addition, product margin floor and NMD core policy-band
overrides. Blank overrides retain the scenario's values and governed rules.
Rule percentages must be between 0 and 100, and the NMD core minimum cannot
exceed its maximum. Preview requires FTP/Confidential/Run. Saving an analysis
requires FTP/Confidential/Create. Both require whole-institution coverage.
Each request is checked by the server; readers see disabled controls and grant
reasons. Saving recomputes and snapshots
the supplied inputs and results. Reopening reads the stored snapshot. Parameter
registers and official regulatory runs are unchanged. Related work: #314, #316
(module run controls), #309 (full stored-run/history views), #289 (journeys).

### Running treasury regulatory scenario sets

Liquidity, Basel Capital, FX and Credit expose **Run all … scenarios** from their
workspaces; the Liquidity, Capital and FX Scenarios tabs offer the same action.
The shell's selected reporting period is the explicit as-of period sent to the
server. The control requires the owning module's Confidential/Run capability
with whole-institution coverage. Readers, missing grants and partial scopes see
a disabled control with a reason. The server independently enforces authority.

The command persists the module's supported regulatory scenario set and shows
success or failure per scenario in the backend's returned order. Credit currently
returns its baseline scenario. A failure in one scenario remains visible beside
successful scenarios. Accepted batches reload the module reads and stored-run
registry; the scenario definitions also refresh. No page read, tab navigation or
period selection mints a run. The current live view stays current; these actions
do not select or promote a stored run. Full stored-history work remains #309;
browser journeys are tracked by #289 and pilot qualification by #330/#331.
