# Stress testing — gap analysis & build spec against the BoG Guideline

**What this is:** a review of AequorOS's current stress-testing implementation (backend + UI)
against the **Bank of Ghana Guideline on Stress Testing, 2026 (Exposure Draft, Feb 2026)**
(`docs/EXPOSURE-Draft-Directive-on-Stress-Testing_FEBRUARY-2026.pdf`), the gaps, and a
buildable target architecture for another agent to implement. **Status: current build is
partial; this is the plan to close it.**

**The authority:** the BoG Guideline. It applies to **RFIs = banks, savings & loans, finance
houses, finance & leasing, FHCs** (¶3) — so it also scopes into `sdi.md` (see §8). **Effective
1 January 2027; RFIs expected to align by 31 December 2026** (¶8–9). Issued under Act 930
s.92(1); read with the Risk Management Directive 2021, the CRD 2018, and the IRRBB Guideline.

**As-built anchor:** 2026-08-19 (directive read in full; backend + UI stress inventories with
`file:line`). **Code wins over this doc.** Companions: `product.md`, `sdi.md`,
`regulatory_reporting.md`, `ai_engine.md`.

**Reference paragraphs** below cite the directive as `¶N` (main body) or `AppI/AppII/AppIII ¶N`.

---

## 0. Executive verdict

The platform has the **compute primitives** (per-module stress overlays, reverse-stress
bisection, forecast projection, VaR) and the **chart primitives** (recharts waterfall, tornado,
threshold lines, projection paths) — but not the **stress-testing *framework*** the directive
requires. Three structural gaps dominate:

1. **No macro-scenario layer.** Every shock in the platform is a **direct risk-parameter
   override** (a run-off %, an RWA-growth %, a bp curve shift) applied **per module,
   independently**. The directive requires **scenarios defined by macroeconomic variables**
   (GDP, inflation, interest rate, FX, unemployment, GoG yields — AppIII, Table 6) translated
   coherently into risk parameters across **all** modules at once (¶34, ¶38, ¶43). This layer is
   **absent**.
2. **No 3-year enterprise projection in the prescribed format.** The directive's core
   deliverable is a **3-year pre/post-stress projection** of capital, P&L, balance sheet and
   RWA, reported in **Appendix II Tables 1–6** (¶67, ¶68, AppII). The platform has a 4-quarter
   capital path and a separate 5-year forecast, but **neither produces the Appendix II tables**,
   and there is no management-actions modelling (with/without — ¶67(f), AppII Table 1).
3. **The stress UI is below product grade.** The four "stress" screens are the **same component
   rendering one HTML table and no charts**; scenario authoring is a raw key/value form; saved
   runs cannot be re-opened; there is no macro scenario, no driver attribution, no user
   narrative/management actions, and no purpose-built board pack.

### Compliance scorecard (directive part → current state)

| Directive area | Requirement (¶) | Current state | Verdict |
|---|---|---|---|
| Governance & framework | Board attestation, Stress Testing Committee, scenario approval, model validation (¶10–29, ¶57–63) | Immutable runs exist; **no stress-run sign-off/challenge, no framework attestation, no runtime scenario approval** | **PARTIAL** |
| Scenario design (macro) | Macro variables, consistent, ≥1 severe downturn, historical + hypothetical, forward-looking (¶34–43, AppIII) | Direct parameter overrides only; **no macro layer**; codes frozen at deploy | **GAP** |
| Sensitivity / scenario / enterprise / reverse taxonomy | Full taxonomy (defs; ¶6, ¶40, ¶50) | Scenario ✓, reverse ✓; **enterprise-wide integrated = absent**; sensitivity = fixed single-factor only | **PARTIAL** |
| Models & methodologies | Fit-for-purpose, justified overlays, range of methods (¶44–47) | Fixed engines; no methodology registry/validation | **PARTIAL** |
| IFRS 9 ECL under stress | PD/LGD/ECL linkage; **Perfect Foresight (3yr)**; **Single Scenario (100% weight)** (¶48–49, AppI¶5–6) | ECL engine exists; **not driven by a stress scenario, no perfect-foresight/single-scenario mode** | **GAP** |
| 3-year projection | Pre/post-stress capital ≥3yr; remain above all minima (¶68, ¶77) | 4-quarter capital path; 5-year forecast; **not 3yr stress projection to Appendix II** | **GAP** |
| Per-risk methods | Credit/market/operational/liquidity/IRRBB/concentration/contingent-leverage/macro (AppI) | Credit (RWA-growth proxy), liquidity, IRRBB, FX present; **operational stress, concentration stress, contingent-leverage = absent** | **PARTIAL** |
| Management actions | Broad set, triggers, **results with & without** (¶78–81, AppII T1) | Deterministic recommended-action **text** only; **not modelled, no with/without** | **GAP** |
| Reporting | Appendix II Tables 1–6; annual ICAAP submission by **end-March**; vulnerability granularity (¶64–67, AppII) | STRESS-PACK + ICAAP-STRESS returns exist (own format); **not the Appendix II tables** | **GAP** |
| ICAAP integration (Part IV, banks only) | Stress within ICAAP; capital+liquidity plans (¶68–77) | ICAAP-STRESS companion return; **no capital-restoration/ICAAP stress projection** | **PARTIAL** |
| UI / product | A usable stress workbench | One table, no charts, write-only saves | **BELOW GRADE** |

---

## 1. What the directive requires (the yardstick)

### 1.1 Taxonomy & definitions (¶5–6, Part I)
- **Sensitivity analysis** — single/limited risk factor, no cohesive narrative.
- **Scenario analysis** — joint movement of many macro/financial parameters, consistent.
- **Enterprise-wide stress test** — the RFI as a whole, not one business line/portfolio.
- **Reverse stress test (RST)** — start from a pre-defined adverse outcome (breach/insolvency/
  illiquidity), find the scenarios that cause it.
- **Bottom-up** (internal models/scenarios) vs **Bottom-up supervisory** (BoG-provided
  assumptions the RFI applies and reports).
- **Base case** vs **Adverse** vs **Historical** vs **Hypothetical** scenarios.
- **Solvency stress** (economic/regulatory capital) vs **Liquidity stress** (cash flow, funding,
  liquid-asset prices). **Second-round / feedback effects** amplify initial shocks.

### 1.2 Governance (Part II, ¶10–29)
Board holds ultimate responsibility; a **Stress Testing Committee** develops/implements; roles
split across **scenario development & approval, model development & validation, reporting &
challenge, use of outputs** (¶16). Policies documented and Board-approved (¶17). **Board attests
it has reviewed and challenged both the framework and the results, with a rationale for their
credibility** (¶20). Annual review; **independent validation** (¶57–63).

### 1.3 Scenario design (¶33–43, AppIII)
- Capture **all material risks** (on/off-balance, earnings, operational, reputational, climate).
- Each scenario built on **macroeconomic variables in a consistent manner**: **GDP, interest
  rate, inflation, FX** (¶34), plus **unemployment, asset prices** (¶43).
- **Severe but plausible**; **various degrees of severity**; **≥1 severe economic downturn**;
  **historical + hypothetical**; forward-looking (geopolitical, natural disaster, climate).
- **Internally coherent** — stressed risk factors translate into internally consistent **risk
  parameters** (¶38(e)).
- Levels: **portfolio, business unit, enterprise-wide** (¶40); RST for vulnerabilities.
- AppIII risk drivers: GDP slowdown, cedi depreciation, interest-rate & inflation moves, cocoa/
  gold price decline, liquidity outflows, funding-cost rise, reputational, climate. Sources: BoG,
  GSS, Bloomberg, IMF, World Bank, Reuters, Fitch, AfDB, EIU.

### 1.4 Models & methodologies (¶44–50)
Fit-for-purpose; **justify all overlays/expert judgement** with challenge/validation (¶45); a
**range of methodologies**; key outputs = **implied losses, solvency (CAR), liquidity** (¶46).
- **IFRS 9 ECL under stress (¶48–49, AppI¶5–6):** demonstrate the link scenario → **PD, LGD,
  ECL**; **Perfect Foresight** — assume accurate prediction of **≥3 years** of macro from day one;
  **Single Scenario** — ascribe **100% probability weight** to the stress scenario.
- Sensitivity (single event) vs scenario (joint) → **asset values, RWA, profitability, capital,
  liquidity, funding** (¶50).

### 1.5 Use, review, reporting (¶51–67)
Regular schedule + ad hoc (¶53); results feed **risk appetite, capital & liquidity planning,
contingency & recovery, ICAAP** (¶55). Annual framework review + independent validation;
**solvency–liquidity interlinkage** (¶59(f)). **RFIs submit annual stress-test results to BoG as
part of the ICAAP in the Appendix II formats by end of March of the ensuing year** (¶67), with:
risks/exposures/entities covered; macro conditions & assumptions; methodologies; impact on
profitability/CAR/liquidity at each balance-sheet date over the horizon (**absolute + ratios**);
management actions; **results with and without management actions** (¶67(f)); vulnerability at
granularity (currency, business line, sector, borrower groups); Board minutes; independent
reviews.

### 1.6 ICAAP integration (Part IV, ¶68–81 — banks only per fn.16)
Project **pre/post-stress regulatory capital ≥3 years** (¶68). Stress the AppI risks →
**NII, NPLs, profitability, investment portfolio, capital** (¶71). **≥1 severe adverse macro
scenario** (severe downturn and/or market-wide + idiosyncratic liquidity shock); **≥3-year
horizon** (¶75). Assess ability to **remain above all regulatory minima: CAR, CET1, Tier1,
leverage, paid-up capital** (¶77). **Management actions** — broad, credible, triggered,
documented, and reported **with and without** (¶78–81).

### 1.7 Per-risk methodologies (Appendix I)
- **Credit & counterparty** — PD, LGD, EAD; market-wide / idiosyncratic (largest counterparty) /
  sector-specific / combined shocks; collateral-value stress; impact on **IFRS-9 impairment + BoG
  provisions, RWA, NII/fees, cost, capital**.
- **Market** — FX, equity, commodity, interest-rate; trading + FVOCI + HTM; liquidity shortage;
  large-participant default.
- **Operational** — IT-infra change, process/product/IT robustness, outsourcing concentration,
  misconduct (>IAS 37); **annual scenario simulations: cloud outage, cyber/data corruption,
  payments outage, telecom failure, staff unavailability, flood/epidemic/civil strife**; BCP
  validation; post-incident review.
- **Liquidity** — macro (rate shocks on buffers/funding cost), funding vulnerabilities, deposit-
  withdrawal spikes, funding concentration, balance-sheet growth; **idiosyncratic + market-wide +
  combined**; **time horizons overnight → 12 months** (intraday, 5d, 30d, 3–12m); tech-accelerated
  outflows; cross-risk (credit/reputational → liquidity, fire sales). **Main method = net cash-flow
  profile**; metrics = liquidity ratios, buffer/**counterbalancing capacity**, **survival
  horizon**, solvency/profitability.
- **IRRBB** — stressed scenarios per the BoG IRRBB Guideline; gap/basis/option risk; earnings +
  capital.
- **Concentration** — single-name (counterparty/connected group), sectoral, geographical,
  product, collateral type, funding source, third-party; on+off balance; banking+trading;
  correlation changes.
- **Contingent leverage** — derivatives/SFTs, collateral swaps, netting → leverage-ratio impact.
- **Macroeconomic** — well-defined, severe-but-plausible, justified.

### 1.8 Output contract (Appendix II — the regulatory deliverable)
- **Table 1 — Summary Results:** Current + 3-year projection. Capital gap; **Pre-Adverse (Base
  Case)** CET1/Tier1/Tier2/Total Reg Cap/RWA + ratios; **Impact of Adverse** losses **by CRD
  exposure class** (GoG, BoG, other sovereigns/central banks, PSE, MDB, banks, other FI,
  corporates, retail/SME, past-due, high-risk, other); **Post-Adverse (Stress Case)** stressed
  RWA/capital/ratios/paid-up; capital required to meet the **13% CAR** and **paid-up** minima;
  **Management actions** (raise capital, dividend, strategy, asset sales, risk reduction);
  **Post-capitalisation**; residual capital required.
- **Table 2 — Regulatory Capital Projection:** full CET1 (paid-up, retained, statutory reserves,
  other reserves, minority) → deductions (intangibles, FI investments, OCI, DTA, commercial-entity
  investment) → CET1 after; AT1 (capped 1.5% RWA); Tier2 (capped 2% RWA); Total; Credit Risk
  Reserve. Current + Base(Y1-3) + Stress(Y1-3).
- **Table 3 — Movement in P&L:** opening retained; interest income/expense → NII; fees; trading;
  other income; non-interest/opex/staff expenses; **impairment losses (incl. stress)**; D&A; PBT;
  tax; PAT; distributions; **adjusted retained earnings for CAR**.
- **Table 4 — Statement of Financial Position:** foreign + domestic assets (cash, short-term
  investments, derivatives, loans, long-term investments, equities, PPE, other); capital; foreign
  + domestic liabilities (**demand/savings/time deposits**, borrowings). Base + Stress, 3yr.
- **Table 5 — Evolution of RWA & Capital Requirements:** RWA per Pillar-1 type (credit/
  operational/market) + Pillar-1 requirement (13% of RWA); Pillar-2 (credit concentration, IRRBB,
  sovereign, country & FX, reputational, other); Total. Base + Stress, 3yr. *(Stressed Total
  Pillar-1 RWA must equal Table 1's stressed RWA.)*
- **Table 6 — Key Risk Drivers & Forecasting Assumptions:** GoG-securities yield, GDP growth,
  interest rates, unemployment, **FX (USD/GBP/EUR → GHS)**, inflation, GSE index, fiscal deficit.
  Base + Stress, 3yr, with sources.

---

## 2. What's built today (with paths)

### 2.1 Backend — 18 stress features, all top-down parameter overrides
- **Liquidity:** `apply_liquidity_stress` (`app/domain/liquidity/engine.py:277`) — run-off/inflow/
  HQLA-haircut/ASF-RSF overrides; behavioural stressed ladder (`:494`); FX-depreciation currency
  gaps (`:413`). Breach multiplier (`app/services/regulatory_liquidity.py:1619`).
- **Capital:** 4-quarter stress path `run_capital_stress` (`app/domain/capital/engine.py:497`,
  `STRESS_QUARTERS=4`) — RWA growth, CET1 retention, FX multiplier; trigger/action ladder (`:564`).
  Breach multiplier (`app/services/regulatory_capital.py:1575`).
- **Reverse stress:** `run_reverse_stress` (`app/services/reverse_stress.py:72`) — bisection for
  the severity *k* breaching a floor, two **independent** axes (liquidity=LCR, capital=CET1);
  immutable `RegulatoryRun`.
- **IRRBB:** 6 Basel ΔEVE scenarios + EaR (`app/domain/irr/engine.py:384,470`). **FX:** depreciation
  scenarios + stressed VaR (`app/domain/fx/engine.py:479,373`). **FTP:** curve/funding stress.
- **Forecasting:** what-if (4 hardcoded macro-flavoured shocks, `forecasting/engine.py:141`);
  adverse scenarios (base/adverse/severely_adverse, 5-yr); strategic optimizer.
- **Returns:** STRESS-PACK (`generation.py:1305`, event-driven, own format) and ICAAP-STRESS
  (`:974`).

### 2.2 The scenario data model (two walled-off stores)
- **System (regulatory) scenarios:** codes **hardcoded** per module (`regulatory_liquidity.py:100`,
  `regulatory_capital.py:108`, …); values in `param_stress_shock` (`app/models/regulatory.py:224`,
  effective-dated + approval columns). **No runtime write path** — `ParamStressShock` is
  constructed only in the fixture + migrations; **magnitudes are frozen at deploy, not editable/
  versionable through the product.** A shock is always a **direct parameter override, never a
  macro variable.**
- **Custom scenarios:** `StressScenario` (`app/models/scenario_workbench.py:41`), full CRUD,
  **"never consumed by official runs"** — sandbox only, results write nothing unless manually
  saved (`SavedScenarioAnalysis`, never a `RegulatoryRun`).

### 2.3 UI — the workbench is a spreadsheet
- The four "stress" routes (`basel/stress`, `liquidity/stress`, `irr/scenarios`, `fx/scenarios`)
  are thin wrappers over **one** component, `components/workbench/ScenarioWorkbench.tsx` (662
  lines), which renders **one HTML `<table>` and zero charts** (`:500-627`).
- Shock authoring is a **raw key/value text form** validated against an allowed-keys *string*
  (`:411-414`). The delta column is computed vs **`results[0]`** — whichever scenario was ticked
  first (`:544`), not a designated base case.
- **Saved analyses are write-only** — list/save/delete hooks only, **no detail/re-open endpoint**
  (`lib/api/hooks.ts:3164`). Reverse-stress renders **two KPIs + a read-only paragraph, no chart**
  (`app/(app)/forecasting/reverse-stress/page.tsx:74,104`). Board pack is chart-free and contains
  no stress content (`app/(app)/reports/board-pack`).
- A capable recharts library (waterfall, tornado, threshold lines, projection paths, gauges)
  exists (`components/forecasting/charts/`, `components/fx/charts/`, `components/irr/charts/`) but
  is **not wired into the stress surfaces**. No heatmap / severity-ladder / sensitivity-matrix
  component exists anywhere.

---

## 3. Target architecture (what to build)

The organising idea: **replace per-module parameter overrides with a governed macro-scenario
that fans out coherently into every engine and rolls up into a 3-year enterprise projection in the
Appendix II format.** Seven components.

### 3.1 Macro-scenario model (the missing spine)
A **first-class, governed Scenario** entity (replacing the frozen `param_stress_shock` +
quarantined `StressScenario` split with one library):
- `scenario_type` ∈ base | adverse | historical | hypothetical | reverse | supervisory (¶5–6).
- **Macro-variable paths** over ≥3 years (the Table 6 drivers): GoG yield, GDP growth, policy/
  market interest rate, inflation, unemployment, FX USD/GBP/EUR→GHS, GSE index, fiscal deficit,
  cocoa/gold price. Base + Stress paths.
- Severity metadata, narrative, source attribution, `institution_type` applicability, horizon.
- **Governance:** versioned, effective-dated, **maker-checker approval** (¶16 scenario approval),
  reusable across runs — an actual **scenario library**. Persist supervisory (BoG-provided)
  scenarios as a `supervisory` subtype for the bottom-up-supervisory path (¶13).

### 3.2 Translation layer (macro → risk parameters)
The satellite step the platform lacks: map each scenario's macro paths to the **risk parameters**
each engine consumes (¶38(e), ¶48). Minimum viable, transparent, and documented per ¶45:
- macro → **PD/LGD** multipliers by segment (credit); macro → **deposit run-off / inflow** (liquidity);
  FX path → **NOP revaluation**; rate path → **IRRBB curve shift**; GDP/inflation → **RWA growth,
  NII, fee income**. Each mapping is an auditable, overridable coefficient set (expert-judgement
  overlays justified and challenged). This is where `ai_engine.md` behavioural models plug in as
  satellite models later; start with documented linear elasticities.

### 3.3 Enterprise orchestrator
One scenario run drives **liquidity + capital + IRR + FX + credit coherently** and produces a
single enterprise outcome (¶40, ¶50). Couples solvency and liquidity (¶59(f)); supports
second-round effects (¶5). Replaces today's independent per-module scenario objects. Output is an
immutable `RegulatoryRun` (keep the existing reproducibility spine — value-based `input_hash`).

### 3.4 3-year projection engine → Appendix II
A dynamic balance-sheet/P&L/capital projection over ≥3 years, base + stress, producing **Tables
1–6 exactly** (§1.8). This is the regulatory deliverable and the ICAAP submission (¶67, ¶68). Must:
- carry the CRD capital build (CET1/AT1/Tier2 with caps + deductions) — Table 2;
- roll P&L into adjusted retained earnings for CAR — Table 3;
- project the balance sheet by the Table 4 line taxonomy;
- evolve RWA by Pillar-1 type + Pillar-2 requirements — Table 5;
- check remaining above **all** minima (CAR 13%, CET1, Tier1, leverage, paid-up) — ¶77.

### 3.5 IFRS-9 ECL under stress (¶48–49)
A stress mode over the existing ECL engine (`app/domain/capital/ecl.py`): scenario → PD/LGD → ECL,
with **Perfect Foresight** (project ≥3 years of macro from day one) and **Single Scenario** (100%
weight). Feeds Table 1's losses-by-exposure-class and Table 3's impairment line.

### 3.6 Per-risk stress methods (Appendix I) — fill the missing ones
Present-and-adequate: liquidity, IRRBB, FX, credit (as RWA-growth proxy — upgrade to PD/LGD/EAD).
**To build:** bottom-up credit (exposure-level PD/LGD/EAD, downgrade migration), **concentration
stress** (single-name/sector/geo default — not just LE reporting), **operational-risk scenario
simulations** (the seven AppI¶12 scenarios), **contingent-leverage** stress. Market-risk trading/
FVOCI/HTM revaluation.

### 3.7 Management-actions modelling (¶78–81)
Model a **library of credible actions** (raise capital, cut dividend, reduce RWA, asset sales,
risk-appetite change) with triggers and timelines; produce results **with and without** (Table 1
"Management actions" + "Post-capitalisation" blocks). Replaces today's deterministic action *text*.

### 3.8 Governance & reporting
- **Stress-run sign-off / challenge workflow** and **Board attestation of framework + results**
  (¶20) — reuse the attestation spine that exists for returns.
- **Annual ICAAP stress submission** in the Appendix II format by **end-March** (¶67) — a new
  return family on the existing lifecycle (maker-checker, package, digests reusable unchanged).
- Analyst/CRO **narrative & assumptions rationale** captured per run (absent today).
- **Data inputs:** a new **`macro_scenarios` reference-dataset kind** (or the governed Scenario
  table) carrying the Table 6 macro paths; the canonical position book (exposure-level, for
  bottom-up credit & concentration); the capital `capital_structure` register; `param_stress_shock`
  becomes the *translated* output, not the authored input.

---

## 4. UI target — a real stress workbench

Rebuild around `components/workbench/ScenarioWorkbench.tsx`, the reverse-stress page, and a new
stress board-pack composer under `app/(app)/reports/`.

1. **Typed scenario builder** — author a scenario as **macro-variable paths** (Table 6 drivers over
   3 years) *and/or* a typed shock palette (parallel/steepener/flattener, per-bucket run-off %,
   per-asset haircut, PD/LGD multipliers) with units and validation — not a raw key/value textarea.
2. **Governed scenario library** — versioned, tagged by type/severity, reusable across modules,
   with the maker-checker approval state visible.
3. **Charts inside the stress surfaces** — wire the existing library in: **ratio-vs-threshold**
   paths (CAR/LCR vs floor over the horizon), **waterfall driver attribution** (stressed CAR
   decomposed into credit-loss vs RWA-growth vs FX), **projection paths** (base vs stress), a
   **reverse-stress frontier plot**, and a **scenario × metric heatmap / severity ladder** (new
   components).
4. **Designated base case** — delta computed vs an explicit base scenario, not `results[0]`.
5. **Persistent, re-openable, versioned run registry** — mirror the forecast run registry; a stress
   result can be pinned, reproduced, and diffed across quarters (add the missing detail/re-open
   endpoint).
6. **N-way comparison + driver drill-down** — scenario matrices, not just two-run A/B; drill from a
   headline stressed ratio to its loss attribution.
7. **Narrative & management-action capture** — per-run analyst/CRO commentary, assumptions
   rationale, and modelled management actions.
8. **Stress board-pack composer** — compose from selected runs + charts + commentary + management
   actions, exporting the **Appendix II tables** and a board-ready PDF (the current board pack has
   none of this).

---

## 5. Phased build plan

**Phase 1 — the scenario spine.** Governed Scenario model (macro paths, versioned, maker-checker)
+ the translation layer (documented elasticities macro→risk params). Unifies the two scenario
stores. *Unblocks everything.*

**Phase 2 — enterprise orchestrator + 3-year projection.** One scenario → all engines → the
Appendix II Tables 1–6 projection, base + stress, immutable run. Remaining-above-minima checks.

**Phase 3 — ECL under stress + management actions.** Perfect-foresight/single-scenario ECL mode;
management-action library with with/without results.

**Phase 4 — per-risk methods.** Bottom-up credit (PD/LGD/EAD + migration), concentration stress,
operational-risk scenario simulations, contingent leverage.

**Phase 5 — governance & submission.** Stress-run sign-off + Board attestation; the annual ICAAP
stress return (Appendix II) on the existing lifecycle; narrative capture.

**Phase 6 — the UI.** Typed scenario builder, charts in the stress surfaces, designated base,
re-openable versioned registry, N-way comparison + drill-down, board-pack composer.

Sequence rationale: the macro-scenario spine (P1) and the projection engine (P2) are the load-
bearing gaps; the UI (P6) is worthless until the scenario model beneath it is real.

---

## 6. SDI scoping (feeds `sdi.md`)

The directive applies to **savings & loans and finance houses** (¶3), on a **proportionate basis**
(¶7, ¶82, AppI¶1). Two scoping facts:
- **Part IV ICAAP is banks-only** (fn.16) — so an SDI runs **solvency + liquidity stress** and the
  AppI risk methods, but **not** the full ICAAP capital-restoration projection. The SDI stress
  scope is the enterprise scenario + liquidity stress (against the **binding LMTD Table 1 ratios**,
  per `sdi.md §4.1`) + a simplified s.29 capital stress — **not** the Basel 3-tier Table 2 build.
- Proportionality: an SDI's material risks are **credit, liquidity, concentration, operational** —
  market/IRRBB/FX are conditional (`sdi.md §3`). Its macro drivers reduce to GDP, inflation, policy
  rate, FX (if any book), unemployment.

`sdi.md` gains a "Stress testing (scoped)" subsection referencing this doc: which stress features
are CORE/CONDITIONAL/EXCLUDED for an SDI, and the SDI stress data inputs (the same canonical book
+ the macro-scenario dataset).

---

## 7. Sources

- **BoG Guideline on Stress Testing, 2026 (Exposure Draft, Feb 2026)** —
  `docs/EXPOSURE-Draft-Directive-on-Stress-Testing_FEBRUARY-2026.pdf` (read in full; ¶ and
  Appendix references throughout). Effective 1 Jan 2027; align by 31 Dec 2026.
- Backend & UI inventories (2026-08-19), cited `file:line` in §2.
- Companions: BoG CRD 2018 (capital definitions, exposure classes — AppII), Risk Management
  Directive 2021, IRRBB Guideline (AppI¶23). `sdi.md`, `product.md`, `ai_engine.md`.
