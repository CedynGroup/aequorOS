# AequorOS Forward Curve Construction — Design & Methodology Specification

*Companion to `AequorOS_Market_Data_and_Curve_Platform.md`. This document
specifies the enterprise, UI-driven Forward Curve Construction capability for
the Market Research Desk (internal) and the Markets platform (bank/client),
reverse-engineered from the real **Eikon Forward Curve Template v3** and built
on top of the existing golden-copy / overlay / methodology-register / bitemporal
architecture. Scope priority: **Rates → Forward Curves** first.*

## As-built Status (2026-08-12)

FC-0 through FC-5 are implemented: the pure calendar/schedule and rate-helper
layers live in `app/domain/curves/`; the simultaneous multi-curve solver and
tenor-adjusted grid are in `multicurve.py`; governed, versioned curve definitions
and the desk construction workspace are live through the operator API and
`console/desk/curves`. The bank-facing Markets workspace supports historical
selection, overlay composition, output-basis conversion, and the exact published
`Start / End / DF / Yield` grid.

The published determination now persists both the compact canonical curve points
used by engines and the exact calendar-adjusted construction grid plus immutable
definition snapshot. `GET /banks/{bank_id}/market-data/curves/{curve_name}/forward-grid`
returns that evidence directly. Older determinations without this stored evidence
remain readable through an explicitly labelled legacy reconstruction; they are not
presented as exact schedule reproduction.

Banks select a published curve definition and an as-of slice, then choose only the
display convention through **Convert to**. Calendar, forward/projection index,
discount curve, payment interval/frequency, curve frequency, interpolation, and
roll convention are displayed read-only from the approved definition. Altering
those construction assumptions is a Track-2 desk action, never an ungoverned
tenant-side calculation.

**Status boundary.** §1 is the pre-implementation gap assessment retained for
decision history. It does not describe the current platform. FC-6 remains partial:
full OIS-market bootstrap, broader cross-currency construction, and further
instrument breadth follow available market data and licensing.

---

## 0. The Eikon template, decoded (the reference contract)

The template is a three-part machine and every screen below maps to it.

**Assumptions tab (the parameter surface):**
- `Currency` = USD, `Calendar` = USA, `Date` = 2023-12-29 (the **as-of / valuation date**).
- An **instrument-family catalog**, each row = a curve-building instrument set with a
  Refinitiv **RIC chain** and its native frequency:
  - `USD - Depo, IRS vs 6M` → `0#USDZ=R` (Semi-Annual)
  - `USD - Swap vs 1M/3M/6M` → `0#USDSBMLZ=R / 0#USDSBQLZ=R / 0#USDSBSLZ=R`
  - `USD - Swap SOFR OIS` → `0#USDSROISZ=R`
  - single-basis LIBOR chains `USD SB 1M/3M/6M/1Y LIBOR`
- A **day-count code table**: Money-market Actual/360 (`MMA0`), Actual/365 (`MMA5`),
  Actual/Actual (`AA`), Actual/360 (`A0`), Actual/365 (`A5`).
- **Three curve definitions** (Curve 1/2/3), each parameterised by:
  `Payment Frequency` (Quarterly/Monthly), `Forward Curve` = projection index
  (**3M LIBOR** vs **SOFR**), `Payment Interval (Months)` (3/1), `Curve Frequency`
  (3M/1M) = the **forward period length of the output grid**.

**Each Curve tab (the build + output):**
- `Swap style` (which instrument set), `Convert to` (output day-count, e.g. MM Act/360),
  a **Holidays** calendar column driving date adjustment.
- **Raw pillar nodes**: RIC-per-pillar · maturity `Date` · `Discount Factor` · `Tenor`
  label from `ON, TN, 1W, 1M, 2M, 3M, 6M, 9M, 1Y, 1Y3M …` out to the long end.
- **The deliverable — "Tenor Adjusted, Interpolated Curves"**: `Start Date | End Date |
  Discount Factor | Yield`, one row per forward period of length = `Curve Frequency`.
  Row 1 is the spot stub (Start=as-of, DF=1, Yield=0); each subsequent row is a
  forward rate over [Start,End] with its discount factor to End (e.g. 5.33%, 5.23%…).

**What the template fundamentally is:** a **multi-curve bootstrap** — deposits at the
short end + par swaps (vs a chosen IBOR tenor) + OIS — solved to discount factors at
pillar dates, interpolated, then **resampled onto a regular forward grid** to emit
Start/End/DF/Yield. `Forward Curve` selects the projection index; the OIS leg is the
discount curve; `Convert to` is the yield's output basis.

---

## 1. HISTORICAL GAP ANALYSIS (PRE-2026-08-12)

### 1.1 What already exists and is directly reusable

| Capability | As-built in AequorOS | Reuse for forward curves |
|---|---|---|
| Pure quant core | `app/domain/curves/` — bootstrap, **Hagan–West monotone-convex**, PCHIP, log-DF, NSS, `forwards.forward_curve`+`qa_forwards`, `ois_step` (meeting-date step), Engle-Granger, immutable `CurveBuildResult` with canonical-JSON `input_digest` | The interpolation/forward/QA machinery is done; extend the **instrument helpers**, not the math |
| Curve object model | `objects.py` — `CurveDefinition` (code/kind/interp/day-count/extrapolation), `CurveNodes`, `SUPPORTED_CURVE_CODES` (AEQ.* scheme) | Becomes the `CurveDefinition` the desk builder persists |
| Day-count | `conventions.py` — ACT/364, ACT/365F, ACT/360, discount↔yield, price↔YTM, compounding | Maps to Eikon `A0/A5/…`; needs the **"Convert to" selectable output basis** + 30/360 family |
| Golden copy + publication | desk-as-vendor: approved determinations publish into every tenant via `pull_runner.execute_pull` as `aequor_desk`, AEQ.* names coexisting with vendor rows | The forward curve publishes exactly here |
| Bitemporal + reproducibility | `desk_determinations` (`cob_date` valid time / `published_at` transaction time, value-based `input_digest`); canonical curves carry `as_of_date` + `ingested_at`; `list_yield_curves` arbitrates | **This is the historical as-of reproduction engine** — a strength over the flat Excel |
| Methodology governance | register with Track-1 weekly application vs Track-2 versioned parameter change, maker-checker, `research_adjustments` | Curve construction params become governed methodology parameters |
| Per-bank overlays | `market_data_overlays` — effective-dated, component-tagged (liquidity/TLP/funding/credit), additive-bps primary, RLS-forced, read-time composition | The bank-side "your adjusted curve" is already built |
| Engine consumption | `get_discount_curve` (AEQ.{ccy}.OIS) + projection preference AEQ.{ccy}.SOV.ZERO; dual-curve EVE/FTP with byte-identical fallback | IRRBB/FTP already consume the discount + projection split the template implies |
| Client + desk UI shells | dashboard **Markets tab** (multi-curve board, rates board, overlay drawer, source chips, as-of); console **Markets Desk** section (determinations, methodology register, QA panel) | The homes for both new experiences exist |

### 1.2 What is missing for a full enterprise forward-curve workflow

1. **A rate-helper / instrument abstraction.** The desk builds *sovereign* curves from
   T-bills + bonds. There is **no deposit / FRA / par-swap / OIS-swap helper**, and no
   par-rate → discount-factor solving for swaps. The Eikon template is fundamentally a
   swap/OIS bootstrap; that helper layer does not exist.
2. **An instrument-set catalog with vendor identifiers.** No equivalent of the
   Assumptions RIC-chain catalog (`0#USDSBQLZ=R`) — a governed registry of *which
   instruments define which curve*, with quote source binding.
3. **A business-day / holiday calendar engine.** Eikon adjusts every pillar and grid
   date against a named `Calendar` (USA) + explicit holiday set. AequorOS has **no
   calendar, no business-day adjustment (Following/ModFollowing), no spot-lag (T+2),
   no schedule generation**. This is the single biggest quant gap for swaps.
4. **Multi-curve as a first-class desk choice.** Engines consume a discount/projection
   split, but the **desk builder does not let an analyst pick `Forward Curve = 3M
   LIBOR` vs `SOFR`, payment interval, and curve frequency** and solve projection-on-OIS-
   discounting simultaneously. The Ghana methodology (MPR-anchored AGD + AGS) is
   single-currency-sovereign; the swap-style multi-curve builder is unbuilt.
5. **The tenor-adjusted forward-grid output artifact.** `forwards.py` gives instantaneous
   forwards + QA; it does **not** emit the resampled **Start/End/DF/Yield** period grid at
   a chosen `Curve Frequency` — the actual thing banks pull.
6. **Selectable output basis ("Convert to").** No UI to render the same curve's yields in
   MM Act/360 vs Act/365 vs Act/Act, nor 30/360 for bond-style output.
7. **Parameterised curve *definitions* as saved, versioned objects.** No "Curve 1/2/3"
   equivalent — named, reusable curve configurations under Track-2 governance.
8. **A desk *construction* console.** The Markets Desk shows determinations and
   methodology; it has **no instrument-grid builder, live re-solve, node/QA inspector, or
   forward-grid preview** — the professional research-desk surface.
9. **A bank-side curve *builder/viewer*** beyond the read-only board: no as-of scrubber
   with guaranteed reproduction, no side-by-side official-vs-adjusted forward grid, no
   methodology transparency drawer.
10. **FX / cross-currency and convexity** (futures vs FRA) — out of first scope, noted for
    completeness.

### 1.3 How it falls short of a Bloomberg/Refinitiv experience today

- **Desk side:** an analyst cannot sit at an instrument grid, toggle an instrument in/out,
  change the projection index, and watch DF/Yield re-solve with QA — the core research-desk
  loop. There is no calendar, so any swap curve would be date-wrong at the pillars.
- **Bank side:** a bank cannot reproduce "the 3M-SOFR forward curve exactly as it stood on
  2024-06-28" through a UI, cannot render yields in their preferred basis, and cannot see
  the instrument set + interpolation + version that produced a number — the auditability a
  model-risk function demands.
- **Both:** the platform's *governance* (bitemporal, maker-checker, register) is **ahead**
  of Eikon; the *construction breadth* (instruments, calendars, multi-curve, forward grid)
  is **behind**. This spec closes construction breadth without weakening governance.

---

## 2. METHODOLOGY — Forward Curve Construction

*Every parameter named here is a **governed methodology parameter** (register value),
not free text. Track-1 applies them weekly; Track-2 changes them under second-line
approval, effective-dated, never rewriting history.*

### 2.1 Instrument universe and selection
A curve is defined by an ordered **instrument set** (the Eikon "Swap style"), each an
element of a governed catalog:
- **Deposits / money-market** at the short end: ON, TN, 1W, 1M, 2M, 3M (Act/360, spot-lag
  per calendar). Ghana analogue: interbank ON + T-bill 91/182/364 clearing yields.
- **FRAs / futures** (optional mid-curve; convexity-adjusted if futures).
- **Par interest-rate swaps** vs a chosen IBOR tenor (1M/3M/6M) — fixed vs floating, at
  par (PV=0). Ghana analogue: none yet (no swap market) → sovereign par-bond proxy.
- **OIS swaps** (the discounting instruments): fixed vs compounded overnight index.
  Ghana analogue: the synthetic **AGD** (MPR-anchored step + observed spread).
Selection rules (governed): min-liquidity gate per pillar, on/off-the-run handling,
duplicate-pillar collapse (volume-weight), stale-quote carry-forward limit.

### 2.2 Day-count and compounding
- **Instrument-native** day counts for solving: MM Act/360 (`A0`), Act/365 (`A5`),
  Act/Act (`AA`), 30/360 for bond legs.
- **Interpolation space:** continuously-compounded zero rates on log-discount-factors
  (positivity-preserving) — the platform default.
- **Output basis — the "Convert to" contract:** yields are rendered in a selectable
  convention (MM Act/360 default, per Eikon `B2`), converted deterministically from the
  internal continuous zero. Same curve, multiple presented bases; DF is basis-invariant.

### 2.3 Bootstrap and interpolation (and why)
- **Multi-curve, simultaneous.** Discount curve (OIS) and projection curve (IBOR-tenor)
  are solved **together** — modern par swaps are quoted on OIS discounting, so a naive
  sequential bootstrap is inconsistent. Use the global/ordered-with-dependencies solver
  (`objects.CurveSet`), each instrument a self-repricing helper (the QuantLib/ORE pattern).
- **Interpolation:** **Hagan–West monotone-convex** on forwards when clean forwards matter
  (the desk default; positivity + locality), **PCHIP** on zeros for thin/sovereign days
  (Lartey–Li, Ghana), **NSS** parametric fallback below the liquid-pillar threshold.
  Log-DF-linear available for OIS steppiness. The method is a governed parameter.
- **Acid test:** every input instrument reprices to its input (par-swap PV≈0, deposit DF
  exact) within tolerance on the finished curve — the same invariant the quant lib pins.

### 2.4 Discount factors and yields
- Bootstrap yields **discount factors at pillar dates**; the interpolator yields DF(t) at
  any date. **Zero yield** z(t): DF(t)=exp(−z·τ) (continuous) → converted to output basis.
- **Forward yield over [Start,End]** (the Eikon P column):
  f = (DF(Start)/DF(End) − 1) / τ(Start,End; convention) for simple MM basis, or the
  compounded form for OIS — this is the number in each grid row.
- **Spot stub:** first row Start=as-of, DF=1, Yield=0 (matches template row 7).

### 2.5 Calendars, holidays, tenor adjustment (the missing engine)
- **Named calendars** (USA, GHIPSS/Ghana, NGN, KES, ZAR) as governed data: weekend rule +
  holiday set (the Eikon Holidays column). Multi-calendar union for cross-currency later.
- **Business-day adjustment:** Following / Modified-Following / Preceding; **spot lag**
  (T+2 default) from as-of to curve start; **end-of-month** rule.
- **Schedule generation:** from as-of, roll by `Payment Interval` using the calendar to
  produce adjusted pillar and **grid** dates. The **forward grid** is generated at
  `Curve Frequency` (3M/1M) with adjusted Start/End per row — this is "tenor adjustment".

### 2.6 Governance: Track-1 vs Track-2
- **Track-2 (rare, second-line approved, effective-dated):** the *curve definition* —
  instrument set, projection index, interpolation method, day-count set, calendar, spot
  lag, roll convention, curve frequency, extrapolation rule. Changing any is a new
  methodology version with rationale; history is never rewritten.
- **Track-1 (weekly maker-checker):** applying the approved definition to **this cob's
  quotes** — analyst confirms "definition vX correctly applied to today's inputs", may add
  bounded `research_adjustments` (override / additive-bps / assumption-note + rationale)
  that enter the package digest but do **not** mutate the register.

### 2.7 Current and historical (as-of) from one methodology
The construction is a **pure function of (as-of quotes snapshot × methodology version ×
calendar-as-of)**. Bitemporal storage gives two independent axes:
- **Valid time** = the curve's as-of/cob date (what the market was).
- **Transaction time** = when AequorOS recorded/corrected it (what we knew).
Reproducing "the curve as it stood on date D" replays the determination whose valid time
is D at the transaction-time slice requested (latest, or as-known-on some earlier date for
restatement audits). The `input_digest` guarantees byte-identical replay. A flat Excel
holds one `Date`; AequorOS holds the whole surface — this is the Bloomberg-style history.

---

## 3. WORKFLOWS

### 3.A Research Analyst / Internal Desk (Track-1 weekly, with construction)

1. **Open / select a curve definition.** Pick a governed definition ("USD 3M-LIBOR
   projection on SOFR discounting", the Curve-1 analogue) or a Ghana AGS/AGD definition.
   The definition fixes instrument set, indices, interpolation, calendar, conventions.
2. **Load the instrument grid for the cob.** The builder pulls each instrument's quote for
   the as-of date from the golden quote layer (desk observations / vendor), showing
   RIC/identifier · tenor · quote · adjusted maturity date · include-toggle · liquidity
   flag — the Eikon Assumptions+pillar view, made live.
3. **Run construction.** Simultaneous multi-curve solve → pillar DFs, then the interpolated
   forward grid at Curve Frequency → **Start/End/DF/Yield** preview, plus the raw pillar
   nodes and the implied zero + instantaneous-forward chart.
4. **Review QA gates (hard, pre-publish):** instrument reprice residuals (par-swap PV≈0);
   **forward positivity + oscillation** (`qa_forwards`); monotone-DF; calendar sanity
   (no grid row crossing a mis-adjusted date); pillar coverage vs threshold (NSS-fallback
   flagged). Any hard-gate failure blocks approval.
5. **Research adjustments (bounded, audited).** If a pillar is stale/dislocated, apply an
   override / additive-bps / assumption-note **with rationale**; the grid re-solves and the
   adjustment enters the package digest. Never edits the register.
6. **Maker-checker.** Analyst (maker) confirms "definition vX applied to cob inputs, QA
   green"; Supervisor (checker, ≠ maker) reviews the grid, QA, adjustments, and approves.
7. **Publish to golden copy.** Approved determination publishes the forward curve into every
   tenant via desk-as-vendor under its AEQ.* code (e.g. `AEQ.USD.SWAP.3ML` / `AEQ.GHS.SOV.FWD`),
   `curve_type=forward`, points as the grid. Bitemporal: valid time = cob, transaction time
   = now. Vendor/other-source curves coexist.
8. **Audit + version stamp.** The determination records the input snapshot (quotes + digest),
   methodology version + calendar version, intermediate nodes, QA results, both signers +
   timestamps, and every adjustment — the full reproducibility record.

### 3.B Bank / Client Side

1. **Select a published curve** by AEQ.* code and currency (or build a view from a
   published base). Curve-type badges (zero/forward/discount) and the synthetic-proxy
   marker on AGD are shown.
2. **Choose an as-of date** (current or any past date) on the historical scrubber. The
   platform returns the exact published curve at that valid time — reproducible, not
   re-derived — with the transaction-time slice noted (as-published vs latest restatement).
3. **Apply private overlays (optional).** Per-tenor, component-tagged additive-bps spreads
   (liquidity / term-liquidity / funding / credit) via the overlay drawer; composition is
   read-time — golden data never mutated, never visible cross-tenant.
4. **View Yield + Discount Factor** on the tenor-adjusted forward grid (Start/End/DF/Yield),
   choosing the output basis ("Convert to"), with official (solid) vs adjusted (dashed)
   overlaid and the transparent arithmetic (base + Σ overlay bps = adjusted).
5. **Reproduce history exactly.** Any past as-of returns the curve as it existed then —
   feeding the bank's own IRRBB/FTP as-of runs with the identical numbers the desk published,
   which is the regulatory-reproducibility requirement the risk engines already demand.

---

## 4. UI/UX SPECIFICATION

*Familiar to Refinitiv users (Assumptions → Curve tabs → Start/End/DF/Yield), modern and
governed. Desk = console Markets Desk; Bank = dashboard Markets tab.*

### 4.1 Desk — Curve Construction workspace (new console surface)
- **Parameter panel (the Assumptions analogue):** Currency · Calendar · As-of Date ·
  Curve Definition (saved) · Projection index (Forward Curve: 3M LIBOR / SOFR / GHS-sovereign)
  · Discount curve (OIS / AGD) · Payment Frequency · Payment Interval (months) · Curve
  Frequency (forward tenor) · Interpolation method · Output basis (Convert to) · Spot lag ·
  Roll convention. Every control bound to a methodology parameter; changing a Track-2
  parameter is a visibly heavier action (amber ceremony) vs a Track-1 run.
- **Instrument grid (the pillar view, live):** rows = instruments (RIC/identifier · tenor ·
  quote · adjusted maturity · DF · include-toggle · liquidity flag · source chip). Toggling
  an instrument re-solves.
- **Results — three linked views:** (a) **forward grid** table Start/End/DF/Yield at Curve
  Frequency (the deliverable, Eikon M:P); (b) **charts** — zero curve, instantaneous forward,
  discount factor; (c) **pillar nodes** table with reprice residuals.
- **QA panel:** each gate pass/fail with its measure (max forward, oscillation ratio, worst
  reprice residual, pillar coverage, calendar check). Publish disabled until hard gates pass.
- **Methodology transparency drawer:** definition version, instrument set, interpolation,
  conventions, calendar version, adjustments — the exact recipe.
- **Lifecycle rail:** draft → compute → review → (maker) submit → (checker) approve →
  publish, with per-bank fan-out results.

### 4.2 Bank — Markets → Curves (extend the existing tab)
- **Curve selector + as-of scrubber:** AEQ.* code + currency; a prominent historical date
  picker with "reproduced exactly as published" affirmation and transaction-time note.
- **Forward-grid results:** Start/End/DF/Yield with output-basis switcher; official-vs-
  adjusted toggle (solid vs dashed accent, chart + table); overlay editor drawer with live
  stacked preview and transparent arithmetic; per-overlay attribution ("set by {bank},
  effective {date}, by {user}"); never another bank's overlay.
- **Methodology transparency (read-only):** instruments used, interpolation, conventions,
  calendar, methodology version + effective date, and a link to the published methodology
  note — the bank's model-risk evidence.

### 4.3 Shared components (reuse)
`ChartFrame` + `lib/chartTheme`, `DataTable`, `SectionCard`, `RangeTabs`, `AttributionChip`,
`SyntheticProxyBadge`, `OverlayDrawer`, the desk lifecycle rail, `MonoChip` for AEQ.* codes.
Jurisdiction formatting via `lib/format` (currency in the code, never hardcoded).

---

## 5. DOCUMENTATION REQUIREMENTS

**Desk side (full):** for each curve definition — instrument set + identifiers, selection
& liquidity rules, day-count/compounding, calendar + roll + spot lag, interpolation method
+ fallback threshold, extrapolation, curve frequency, QA gate definitions and tolerances,
the Track-2 change history (who/when/why, effective dates), and per-cob the applied version
+ adjustments + QA results + signers. Everything needed to defend a number to a regulator.

**Bank side (transparent, read-only):** which instruments and source, interpolation method
in plain language, day-count/conventions, calendar, the methodology version + effective
date that produced their curve, a change-log of methodology versions they've consumed, and
(for overlays) their own component breakdown and arithmetic. Enough for the bank's model-risk
function to sign off; never AequorOS-internal thresholds that are competitively sensitive
beyond what governance requires.

Both surfaces read the **same methodology register** — one source of truth, two lenses.

---

## 6. ARCHITECTURE ALIGNMENT

- **Golden copy + overlays:** published forward curves are golden (`canonical_yield_curves`,
  `curve_type=forward`, AEQ.* names) written only via desk-as-vendor `execute_pull`; bank
  overlays compose at read time via `market_data_overlays`. Unchanged model.
- **Bitemporal:** `desk_determinations` (valid = cob, transaction = published_at) + canonical
  `as_of_date`/`ingested_at` deliver as-of reproduction and restatement without history loss;
  `input_digest` guarantees byte-identical replay.
- **Methodology register:** curve definitions become Track-2 versioned parameter sets; the
  weekly build is a Track-1 determination stamped with the version.
- **Risk engines:** IRRBB (`get_discount_curve` AEQ.{ccy}.OIS + projection AEQ.{ccy}.SOV.ZERO,
  dual-curve EVE/duration with byte-identical fallback) and FTP (transfer curve prefers the
  desk sovereign) consume these curves already; the new builder simply produces richer,
  swap-style forward curves under the same codes — no engine seam changes required.

---

## 7. IMPLEMENTATION ROADMAP (Rates → Forward Curves first)

**FC-0 — Calendar & schedule engine (foundational, unblocks everything).**
Governed calendars (USA + GHIPSS/Ghana seeded), weekend + holiday sets, business-day
adjustment (Following/ModFollowing/Preceding), spot lag, end-of-month, schedule generation.
Pure, in `app/domain/curves/`, golden-value tests against the Eikon adjusted dates.

**FC-1 — Rate-helper / instrument layer.** Deposit, FRA, par-swap, OIS-swap helpers with
par→DF solving; instrument-set catalog (governed, with identifiers). Reprice-to-input acid
tests. Reuse the existing solver/interpolators.

**FC-2 — Multi-curve solve + forward-grid output.** Simultaneous projection-on-OIS-discount
`CurveSet`; resample to Start/End/DF/Yield at Curve Frequency; selectable "Convert to" output
basis. Determinism + reproducibility tests; reproduce a template curve within tolerance.

**FC-3 — Curve definitions as governed objects.** Persisted, versioned "Curve 1/2/3"
definitions in the methodology register (Track-2), Track-1 weekly application.

**FC-4 — Desk Construction workspace (console).** Parameter panel + live instrument grid +
forward-grid/charts/nodes + QA panel + methodology drawer + lifecycle to publish.

**FC-5 — Bank Curves experience (dashboard).** As-of scrubber with guaranteed reproduction,
forward-grid viewer with output-basis + official-vs-adjusted, overlay editor, methodology
transparency drawer.

**FC-6 — Hardening & breadth.** Convexity (futures), cross-currency/FX-forward curves,
additional calendars/currencies, entitlement tiers on published curves.

*Ghana-first note:* FC-0/1/2 land the **sovereign** forward curve (AGS) properly on a real
calendar and the Start/End/DF/Yield grid first; the swap-style USD multi-curve (matching the
template exactly) follows once swap instruments/quotes are in scope — the abstraction is
built to accept both from day one.
