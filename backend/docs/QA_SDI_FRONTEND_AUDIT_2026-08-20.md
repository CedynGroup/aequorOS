# QA Audit: SDI Frontend Scope and Workflows

**Date:** 2026-08-20

**Scope:** The tenant dashboard in `backend/dashboard`, reviewed against [docs/sdi.md](docs/sdi.md), with [QA_SDI_STRESS_AUDIT_2026-08-20.md](QA_SDI_STRESS_AUDIT_2026-08-20.md) as the companion backend and stress audit. This review assesses user-visible SDI workflows, route/subtab scoping, information architecture, and API wiring. It is not a visual-design review from rendered screenshots and does not change production code.

**Relationship to the build handoff:** [docs/sdi_ui_handoff.md](docs/sdi_ui_handoff.md) is the implementation handoff: it names the three approved pages, the reusable service functions, the intended endpoints, and the live-tenant test setup. This document is an independent audit of the current dashboard surface. Where the two differ, the handoff's explicit regulatory constraint controls: do not invent an SDI liquidity-stress methodology before BoG publishes one.

## Verdict

The SDI frontend is **partially scoped, not product-complete**.

The platform now does the important shell work correctly: the active institution type controls sidebar items, command-palette entries, module tabs, and a route guard. An SDI cannot navigate to FX, FTP, Positions, Basel RWA, Capital Structure, Basel Stress, Basel Planning, Liquidity Buffer, or NSFR.

That is necessary control, but it is not the SDI experience promised by `docs/sdi.md`. Apart from the simplified capital overview, the in-scope screens are mostly generic bank/Basel workspaces with selected tabs hidden. The central SDI liquidity and stress workflows are either still Basel-oriented or explicitly render a placeholder saying the SDI result is pending.

**Do not represent the frontend as an SDI-ready product until the P0 items below are resolved.**

## What Is Genuinely Implemented

### Institution scope controls are real

- [backend/dashboard/lib/modules.ts](backend/dashboard/lib/modules.ts) maps routes to module keys, restricts unknown scopes to a conservative core set, hides the six SDI-excluded subroutes, and suppresses BSD deep links for SDIs.
- [backend/dashboard/components/shell/Sidebar.tsx](backend/dashboard/components/shell/Sidebar.tsx), [backend/dashboard/components/shell/CommandPalette.tsx](backend/dashboard/components/shell/CommandPalette.tsx), and [backend/dashboard/components/shell/ModuleTabs.tsx](backend/dashboard/components/shell/ModuleTabs.tsx) consume the same visibility helper.
- [backend/dashboard/components/shell/ModuleGuard.tsx](backend/dashboard/components/shell/ModuleGuard.tsx) blocks direct navigation to excluded routes after the tenant scope resolves.

### Simplified capital is a proper SDI replacement

[backend/dashboard/app/(app)/basel/page.tsx](backend/dashboard/app/(app)/basel/page.tsx) switches an SDI from the Basel page to [backend/dashboard/components/basel/SdiCapitalView.tsx](backend/dashboard/components/basel/SdiCapitalView.tsx). The replacement wires dedicated SDI API hooks and presents:

- Section 29 CAR with Net Own Funds and simplified RWA bands.
- Paid-up-capital and statutory-reserve-fund checks.
- NBFI loan classification and provisioning.
- Pending-BoG parameter status, rather than presenting unconfirmed risk weights as settled policy.

The capital subtab layout also correctly leaves the SDI with only the overview; Basel RWA, structure, stress, and planning routes are hidden and route-guarded.

## SDI Route and Subtab Matrix

| Area | SDI scope result | UI readiness | Audit result |
|---|---|---|---|
| Shell navigation | Bank-only modules are hidden and guarded | Scoped | Pass |
| Regulatory Capital `/basel` | Basel-only subtabs removed | Dedicated `SdiCapitalView` | Pass, with follow-up integration gaps |
| Liquidity Cockpit `/liquidity` | Buffer and NSFR hidden | Still Basel LCR/NSFR dashboard | **P0 fail** |
| Liquidity Forecast `/liquidity/forecast` | Kept | No SDI-specific evidence found | P1 gap |
| Liquidity Stress `/liquidity/stress` | Kept | Header and journey remain LCR/NSFR-oriented | **P0 fail** |
| Liquidity Monitoring `/liquidity/monitoring` | Kept | Governance tables, not an SDI liquidity cockpit | P1 gap |
| Liquidity CFP `/liquidity/cfp` | Kept | Reusable governance surface | Needs SDI workflow validation |
| Risk & Limits `/risk` | FX/FTP fetches are scoped off | Still extracts bank capital and Basel LCR/NSFR limits | **P0 fail** |
| Enterprise Stress workbench | Basel liquidity KPI is suppressed for SDI | SDI liquidity is explanatory placeholder; Basel capital charts remain | **P0 fail** |
| Stress board pack | Labels LMTD when LCR is null | Placeholder, and cover still says ICAAP | **P0 fail** |
| Regulatory Reporting `/submissions` | Backend filters return family | Generic workspace gives no visible SDI family/track context | P1 gap |
| Reports and home | Some LCR/NSFR visuals are suppressed | No Table-1 / reserve / SDI exposure equivalent | P1 gap |
| IRRBB, Behavioural, Forecasting, Markets | Included by default SDI module set | Generic shared screens | P2: needs an explicit SDI relevance review, not a separate UI by default |

## Findings

### P0 - SDI Liquidity Cockpit is still a Basel LCR/NSFR page

**Evidence:** [backend/dashboard/app/(app)/liquidity/page.tsx](backend/dashboard/app/(app)/liquidity/page.tsx) renders a `Liquidity Coverage Ratio` gauge, `Net Stable Funding Ratio` gauge, HQLA, ASF, RSF, Basel 30-day outflows, Basel inflow cap, and Basel regulatory floors. It has no `institutionClass === 'sdi'` branch.

**Impact:** An SDI user lands on a primary in-scope module and is presented with the two measures that the SDI specification says are bank-only. Hiding the Buffer and NSFR tabs does not make the remaining Cockpit compliant, because the Cockpit itself leads with LCR and NSFR.

**Required UI:** Replace the SDI Cockpit branch with a first-class LMTD dashboard:

- Table 1's eight binding ratios, their SDI floors, source-as-of time, and status.
- Primary and secondary liquidity-reserve checks.
- Maturity-mismatch ladder and cumulative gap.
- Funding concentration, Top-20/100 depositors, and connected-group exposure.
- Counterbalancing capacity and unencumbered asset haircut view.
- EWI, behavioural stressed ladder, 90-day cash-flow, and CFP action status.

The bank Cockpit can remain unchanged, but it must not be the SDI render path.

### P0 - Enterprise stress describes an SDI regime but does not show one

**Evidence:** [backend/dashboard/components/stress/EnterpriseStressWorkbench.tsx](backend/dashboard/components/stress/EnterpriseStressWorkbench.tsx) correctly removes the Basel LCR KPI when an SDI run has no LCR. It then substitutes a chart-sized message stating that Table 1 and the maturity ladder are the SDI liquidity result and that the stressed-ladder replacement is pending. The same component still displays CET1 and Tier 1/leverage charts for the SDI path.

The run configuration also defaults `carTarget` to `13`, labels the reason placeholder `Quarterly ICAAP stress`, and offers IRRBB/FX inclusion without adapting the language to the SDI's proportionate risk scope.

**Impact:** The user sees an apparently complete enterprise-stress workbench but cannot inspect, compare, or govern the SDI liquidity outcome. Basel capital terms remain on the SDI result screen despite the simplified Section 29 regime.

**Required UI:** Create an SDI results branch with:

- Section 29 CAR path and paid-up-capital/statutory-reserve minima, not CET1/Tier 1/leverage paths.
- An explicit `not assessed` state for SDI liquidity stress that names the missing BoG methodology, shows the last available baseline LMTD liquidity evidence, and does not create a false pass/fail conclusion.
- SDI-specific binding-minima and breach narrative for the measures that are actually assessed.
- SDI risk toggles and copy, with IRRBB/FX only when material and enabled for the licence class.

Do **not** fabricate stressed Table-1 paths, a run-off ladder, survival horizon, or a liquidity breach conclusion until BoG publishes the corresponding SDI stress methodology. Once that method exists, these become the required SDI liquidity-stress visualizations.

### P0 - The liquidity stress route and board-pack preserve bank framing

**Evidence:** [backend/dashboard/app/(app)/liquidity/stress/page.tsx](backend/dashboard/app/(app)/liquidity/stress/page.tsx) says the route couples an `LCR/NSFR path` to the solvency outcome. [backend/dashboard/app/(app)/reports/stress-board-pack/page.tsx](backend/dashboard/app/(app)/reports/stress-board-pack/page.tsx) changes the liquidity KPI text to `LMTD` for SDI runs, but its liquidity chart is only explanatory text and its cover is headed `ICAAP Stress Test`.

**Impact:** The primary journey and board artifact use a bank/ICAAP framing for a proportionate SDI stress exercise, while omitting the actual SDI liquidity evidence the board needs to review.

**Required UI:** Adapt route headers, run labels, board-pack title, executive summary, charts, and conclusion language for SDIs. Until a BoG SDI liquidity-stress method exists, the board pack must present a controlled `not assessed` disclosure plus baseline LMTD evidence, rather than a non-result note that can be mistaken for a stress outcome. It must not manufacture a stressed liquidity conclusion.

### P0 - Risk & Limits is not fed by the SDI control surfaces

**Evidence:** [backend/dashboard/app/(app)/risk/page.tsx](backend/dashboard/app/(app)/risk/page.tsx) scopes FX and FTP requests, but always calls the generic bank `useLiquidityDashboard` and `useCapitalDashboard`. [backend/dashboard/components/risk/limits.ts](backend/dashboard/components/risk/limits.ts) extracts only Basel LCR/NSFR from liquidity and generic bank CAR from capital. It does not call the SDI capital hooks, SDI liquidity views, paid-up capital checks, statutory reserve checks, loan-classification results, or SDI large-exposure checks.

**Impact:** The platform's command-center risk surface can show no SDI control breaches even when the SDI Capital page shows an adverse outcome. Its liquidity wall is based on inapplicable LCR/NSFR measures.

**Required UI:** Add an SDI-specific limit adapter and validation section that consumes the SDI APIs and future liquidity cockpit result, then presents:

- Section 29 CAR, paid-up capital, statutory reserve, and provisioning status.
- Table 1, reserve, maturity, concentration, and counterbalancing-capacity exceptions.
- Grouped single-obligor / large-exposure / related-party findings when available.

### P1 - Liquidity Monitoring is a register page, not the required SDI supervisory workspace

**Evidence:** [backend/dashboard/app/(app)/liquidity/monitoring/page.tsx](backend/dashboard/app/(app)/liquidity/monitoring/page.tsx) identifies SDI threshold rows as binding and includes cash-flow and stressed-ladder panels. Its main content remains the threshold register, haircut schedule, FX funding gap, and a narrative link to the LMT return. It does not display the eight Table-1 ratio values, their individual thresholds, maturity ladder, funding concentration, Top-20/100 depositors, or counterbalancing-capacity calculation.

**Impact:** The most SDI-aware liquidity page makes the binding measures discoverable only indirectly through a regulatory return; a treasurer cannot operate from it.

**Required UI:** Retain this page as **Policy & Assumptions** or fold it into the SDI Liquidity workspace. Do not use it as the substitute for a live SDI cockpit.

### P1 - Regulatory Reporting is functionally filtered but does not communicate the SDI filing context

**Evidence:** [backend/dashboard/app/(app)/submissions/returns/page.tsx](backend/dashboard/app/(app)/submissions/returns/page.tsx) selects from the templates returned by the API and therefore benefits from backend return-family filtering. The UI itself labels neither the active institution type nor its return track/family, and does not explain why a return is present or unavailable.

**Impact:** The user cannot verify from the frontend that the return selector is SDI/BSD-track scoped rather than merely a smaller list. This matters especially while the exact SDI return pack remains a regulator-provided artifact.

**Required UI:** Add an institution-type/return-family badge, the active regulatory track, and an explicit SDI return readiness state. When no licensed template exists, display a controlled `awaiting BoG template` state rather than an ambiguous empty selector.

### P1 - Home and trend surfaces remove Basel metrics without replacing liquidity insight

**Evidence:** [backend/dashboard/components/home/RatioTrendChart.tsx](backend/dashboard/components/home/RatioTrendChart.tsx) hides LCR/NSFR for SDIs and shows only Section 29 CAR. This prevents an incorrect metric, but it replaces the liquidity history with nothing. `PulseWall` and other generic cards are scoped by module, not supplied with SDI liquidity signals.

**Impact:** An SDI executive landing on Command Center has no trend-level view of the binding liquidity regime.

**Required UI:** Add SDI liquidity-summary cards and trends: the selected critical Table-1 ratios, reserve position, survival horizon, concentration warning, and current maturity-gap status.

### P1 - Capital is a strong first SDI screen but not joined to its governance destinations

**Evidence:** [backend/dashboard/components/basel/SdiCapitalView.tsx](backend/dashboard/components/basel/SdiCapitalView.tsx) shows capital checks and loan classification in isolation. It does not provide drill-through to active Alerts, Risk & Limits, exposure concentration, regulatory returns, stress results, or parameter provenance/history.

**Impact:** A user can see a failed simplified-capital check but cannot follow the operational path from issue to alert, evidence, remediation, and regulatory filing.

**Required UI:** Link each failed/no-data card to its control/finding, applicable parameter, affected return, and relevant stress/management-action view.

### P2 - Several retained SDI modules are shared screens without an explicit SDI contract

**Evidence:** The default SDI scope retains IRRBB, Behavioural, Forecasting, and Markets. The audit found module filtering and no equivalent SDI-specific component branch for these areas. This is not automatically wrong: `docs/sdi.md` intentionally keeps them for a deposit-taking SDI. However, the UI does not visibly state the SDI purpose, materiality criterion, or no-data path for each.

**Impact:** A savings-and-loans user can still experience the dashboard as a universal-bank shell with a few hidden pages.

**Required UI:** Do not fork these modules. Add institution-aware copy, eligibility/readiness panels, and proportionate default views. For example: deposit stability/NMD for Behavioural; banking-book repricing for IRRBB; deposit/loan projection for Forecasting; and GHS curve / GoG securities relevance for Markets.

## Subtab Assessment

### Correctly excluded for SDI

- `/liquidity/buffer`
- `/liquidity/nsfr`
- `/basel/rwa`
- `/basel/structure`
- `/basel/stress`
- `/basel/planning`

These exclusions are present in [backend/dashboard/lib/modules.ts](backend/dashboard/lib/modules.ts), filtered out by module tabs, and denied by the route guard. This is the strongest part of the implementation.

### Retained but requiring SDI-specific redesign or validation

| Subtab | Current role | SDI decision |
|---|---|---|
| Liquidity Cockpit | Basel LCR/NSFR and HQLA | Replace with SDI live liquidity cockpit |
| Liquidity Forecast | Shared cash-flow forecast | Retain; validate an SDI maturity/survival-horizon branch |
| Liquidity Stress | Enterprise workbench with Basel framing | Retain; replace with SDI stress outcomes |
| Liquidity Monitoring | Threshold/haircut governance | Retain as policy/assumptions, not the main cockpit |
| Liquidity CFP | Shared contingency-funding governance | Retain; add SDI activation / reserve / funding-concentration context |
| Capital Overview | Simplified Section 29 | Retain; add governance drill-through |
| Reporting sections | Shared lifecycle | Retain; expose SDI return-family context |

## Recommended Frontend Delivery Order

1. **Stop showing Basel liquidity to SDIs.** Add the SDI Liquidity Cockpit and make it the `/liquidity` render path before adding more shared polish.
2. **Make Risk & Limits institution-aware.** Route the SDI capital and liquidity findings into the executive control surface and Alerts.
3. **Build actual SDI enterprise-stress results and board-pack sections.** Remove all placeholder charts and Basel/ICAAP language from SDI runs.
4. **Complete the regulatory filing and evidence flow.** Add SDI return-family state and drill-throughs from capital/liquidity checks to governance artifacts.
5. **Refine retained shared modules.** Make IRRBB, Behavioural, Forecasting, and Markets feel proportionate to a deposit-taking SDI rather than like inherited bank pages.

## Verification Performed

- Inspected the scope registry, sidebar, command-palette integration, module tabs, and route guard.
- Inspected every scoped liquidity and capital subtab definition.
- Inspected the actual SDI render branch for capital and the absence of an equivalent branch on the liquidity cockpit.
- Inspected Risk & Limits limit extraction, enterprise stress result rendering, stress board-pack rendering, home trend rendering, and returns workspace selection.
- Searched all dashboard TypeScript/TSX sources for SDI-specific branches and components. The substantive SDI-specific surface found was `SdiCapitalView`; the remaining SDI-specific stress references suppress Basel claims or render explanatory placeholders rather than a distinct outcome.

## Release Assessment

**Do not call the frontend SDI-ready.** The top-level and subtab scoping is sound, and the simplified Capital view is a credible first vertical slice. But the binding SDI liquidity regime, enterprise stress outcome, executive risk controls, and board evidence are not yet implemented as usable UI. The product currently behaves as a partially filtered universal-bank dashboard rather than a complete SDI workspace.