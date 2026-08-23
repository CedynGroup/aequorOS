# AequorOS Forensic Calculation Architecture Audit

> ## ⚠ HISTORICAL — its headline finding stands; one premise is superseded.
>
> **Added 2026-08-22 (WS-A12).** Preserved as written. Findings are tracked in
> `docs/audit/remediation_master_register.md` and re-audited independently in
> `backend/docs/INDEPENDENT_FORENSIC_REAUDIT_2026-08-22.md` (`D-1` … `D-32`).
>
> **Still correct, and the sharpest framing anyone produced:** the four-plane taxonomy in §1
> and §2. Nothing in the remediation collapsed the four planes, nor should it. What changed is
> that three of the four boundaries are now guarded.
>
> **Superseded:** §1's statement that the case/regulatory boundary is *"enforced mostly by
> schema/route separation and convention rather than a dedicated architectural guard."* It is
> now a derived, closure-based guard over all of `app/` —
> `backend/tests/architecture/_planes.py` (392 lines) computes plane membership once from
> `CASE_PLANE_GLOBS`, and the same frozenset is both the owner allow-list and the
> forbidden-import set. See register `CF-2`/`CF-4`/`ARCH-5` and the 2026-08-22 evasion-audit
> correction that hardened it.
>
> **Also resolved, deliberately not by equating anything:** the "confirmed semantic
> divergence" on forecast year-0 CAR. Both sides call the same `compute_capital_ratios`; they
> were being handed different fact sets. Equality now holds at `Decimal(0)` tolerance
> (`backend/tests/equivalence/`), and the registry designation was **still left
> `ADVISORY_ONLY`** — numeric equality under a tested baseline is not an enforced identity.
> That is the right end state, not an open item.

**Date:** 2026-08-21  
**Method:** Code-level, read-only trace of executable calculation engines, service orchestration, storage models, API consumers, reporting generators, client calculations, tests, and policy resolution.  
**Scope status:** Material calculation surface covered in this pass: bank-scoped regulatory ALM, SDI calculations, case-scoped financial workspace, scenario workbench, reporting/package generation, fact derivation, client arithmetic, and the primary policy resolvers. A cell-by-cell equivalence proof for every registered BoG template and every market-desk calculation remains `UNKNOWN`; it is a distinct follow-up workstream.

## 1. Executive Finding

# DUPLICATE CALCULATION IMPLEMENTATIONS EXIST

There is **not one single executable calculation pipeline** in the repository.

There are at least four calculation planes:

1. **Bank-scoped regulatory / treasury plane**: `BankFinancialFact` plus governed parameter rows feed pure capital, liquidity, IRR, FX, FTP, forecast, stress, and rating engines. Runs persist as immutable `RegulatoryRun` records.
2. **SDI regime plane**: direct canonical-position / canonical-reference calculations for Act 930 s.29 capital, LMTD liquidity, loan classification, reserves, concentration, and counterbalancing capacity. It is intentionally not the Basel capital/liquidity engine.
3. **Case-scoped financial workspace plane**: `financial_*` case records plus `RiskScenario` assumptions feed an independent balance-sheet forecast, cash-flow adequacy analysis, and equity/assets pressure calculation. Runs persist as `CalculationRun` / `CalculationForecastPeriod` / `CapitalProjection`.
4. **Regulatory-reporting template plane**: BoG template input resolvers read facts, canonical positions, references, and selected run outputs, then evaluate the official workbook's formulas. Packages persist as immutable `RegulatoryPackage` snapshots.

The existence of multiple planes is **not automatically a defect**. The SDI and universal-bank planes calculate different legal regimes, and the case plane has not been shown to feed regulatory filing. However, there are confirmed and material architectural risks:

- **Confirmed semantic divergence:** a bank-scoped forecast run's Year-0 CAR can differ from the capital run's CAR for the same bank/period because the forecast snapshot excludes `ecl_exposure`, while capital may include modeled ECL. This is explicitly asserted in `tests/api/test_forecasting.py`.
- **Confirmed independent reporting computation:** template-based BoG returns calculate from mapped facts/positions and the workbook's formulas. Some returns consume run lines/metrics; others compute cells directly from facts. Equivalence to the relevant operational engine is not uniformly proved.
- **Confirmed independent case formulas:** case “liquidity” and “capital” calculations are mathematically different from regulatory LCR/NSFR/CAR. They do not currently feed filings, but their overlapping names create a material interpretation and ownership risk.
- **Policy split:** universal-bank engines use legacy tenant `Param*` tables through `params.get_active_params`; SDI engines use the newer global `RegulatoryParameter` resolver. Some stress/fact-derivation defaults remain code constants.

**No evidence found in this pass that `CalculationRun`, `CalculationForecastPeriod`, `CapitalProjection`, or case liquidity findings feed a regulatory package or filing.** That is a critical non-competition boundary, but it is presently enforced mostly by schema/route separation and convention rather than a dedicated architectural guard.

## 2. Calculation Planes

| Plane | Scope and storage | Material calculations | Output authority | Reporting / filing use | Verdict |
|---|---|---|---|---|---|
| Bank regulatory | `banks`, `bank_reporting_periods`, `BankFinancialFact`, `RegulatoryRun` | LCR, NSFR, RWA, CAR, CET1, Tier 1, leverage, IRRBB, FX NOP/VaR, FTP, regulatory forecast, reverse stress | Authoritative for universal-bank ALM/regulatory metrics | Yes | Primary regulatory plane |
| SDI regime | Canonical positions / references, `RegulatoryParameter` | NOF/RWA CAR, LMTD Table 1, reserves, maturity ladder, exposures, loan grades | Authoritative for SDI-specific metrics | SDI filing family deferred pending BoG pack | Separate legal regime, not duplicate Basel |
| Case financial workspace | `risk_cases`, `financial_*`, `RiskScenario`, `CalculationRun` | Generic balance-sheet cash projection, cash coverage, credit reliance, equity/assets pressure | Authoritative only for case analysis | No evidence of filing use | Legacy/advisory plane |
| Reporting templates | `RegulatoryPackage`, BoG layouts/linemaps | Resolver aggregation plus official workbook formula evaluation | Authoritative for template-form values | Yes | Independent reporting calculation plane; equivalence partial |
| Scenario workbench | Bank facts/params, no `RegulatoryRun` by default | Calls the same bank regulatory engines for transient/scenario comparison | Advisory/transient | No | Safe scenario duplication, proven equal for tested metrics |

## 3. Master Material Calculation Inventory

| Domain | Metric / output | Scope | Implementation | Inputs | Policy / parameters | Consumers | Authoritative? | Duplicate classification | Risk |
|---|---|---|---|---|---|---|---|---|---|
| Liquidity | LCR | Universal | `app/domain/liquidity/engine.py::compute_lcr`; orchestrated by `regulatory_liquidity` | `BankFinancialFact` securities, balance-sheet, OBS, inflows | `ParamLcrRunoffRate`, `ParamLiquidityThreshold` via `params.get_active_params` | Live cockpit, RegulatoryRun, LCR-NSFR package, stress, forecast | Yes for universal LCR | Scenario workbench reuses engine; reporting LCR-NSFR consumes run preview | Low for run-backed package; see reporting caveat |
| Liquidity | NSFR | Universal | `compute_nsfr` | Bank facts for ASF/RSF | `ParamNsfrWeight`, thresholds | Cockpit, package, forecast, stress | Yes for universal NSFR | Scenario workbench reuses engine | Low |
| Liquidity | Currency gaps / stressed ladder | Universal | `domain/liquidity/engine.py::compute_currency_gaps`, `compute_stressed_ladder` | Bank facts / current facts | Liquidity shocks and behavioral assumptions | Liquidity run, monitoring/stress UI | Yes | No separate verified formula | Medium: behavioral assumption governance |
| Liquidity | Table 1 liquidity ratios, reserves, maturity ladder, concentration, capacity | SDI binding; bank monitoring where applicable | `regulatory_reporting/le_generation.py` helpers used by `sdi_views.py` | Canonical snapshots/references | `RegulatoryParameter` + board threshold rows | SDI liquidity/monitoring/report views | Yes for SDI LMTD measures | Shared helper usage, not duplicate | Low for formula; pending SDI stress methodology |
| Capital | Universal RWA | Universal | `domain/capital/engine.py::compute_rwa` | Bank facts: credit, market FX, operational income, OBS | risk-weight, BIA, FX/rwa params | Capital run/dashboard, forecasting, stress, reporting preview | Yes | Scenario workbench reuses engine | Low |
| Capital | Universal CAR, CET1, Tier 1, leverage | Universal | `compute_capital_ratios` | Capital components + RWA + optional ECL | capital thresholds / GP cap | Capital run/dashboard, forecast, stress, CAR-RWA package | Yes | Forecast year 0 uses projected fact set | **High: forecast-vs-capital divergence** |
| Capital | SDI CAR / NOF / simplified RWA | SDI | `sdi_capital.py::compute_sdi_capital_summary` | Canonical `capital_structure`, canonical positions | `RegulatoryParameter` `car_min`, simplified risk weights | SDI capital, exposures, stress | Yes for s.29 | Different legal regime from Basel | Medium: pending weight fallback |
| Capital | Case solvency pressure | Legacy case | `services/capital.py::_indicator` | `CalculationForecastPeriod` | hard-coded 10% / 20% equity/assets thresholds | Case capital projection | Case-analysis only | Near-duplicate concept, different formula | Medium naming/ownership |
| Credit | Loan classification / provisioning | Bank & SDI class-specific | `domain/capital/loan_classification.py`, `services/loan_classification.py` | Canonical loans, DPD/stage | class-keyed provisioning parameter rows | Loan book, SDI capital, ECL/reporting inputs | Yes | Shared engine with class rules | Low |
| Credit | ECL | Shared bank/SDI where inputs exist | `domain/capital/ecl.py::compute_ecl` | ECL exposure facts plus ECL assumptions | ECL assumption register | Capital run; stress conditioning | Yes | No competing implementation found | Medium: default / missing-data behavior |
| IRRBB | Gap, duration, EVE, EaR/NII | Bank and eligible SDI | `domain/irr/engine.py` via `regulatory_irr.py` | IRR positions, curves, Tier 1 | IRR parameters/shocks | IRR run, dashboard, stress/workbench | Yes | Workbench calls same service logic | Low for duplication |
| FX | NOP, VaR, stressed VaR | Universal | `domain/fx/engine.py` via `regulatory_fx.py` | FX positions / rate history | FX thresholds and shocks | FX run/dashboard, BSD forms where configured | Yes | Workbench reuses service logic | Low |
| FTP | Product margin, contribution, curve | Universal | `domain/ftp/engine.py` via `regulatory_ftp.py` | FTP facts / curves / behavioral assumptions | FTP rules/parameters | FTP run/dashboard | Yes | Frontend business-line implied-margin grouping, labelled presentational (§5.1) | Medium presentational shadow — resolved 2026-08-22 |
| Forecast | Regulatory bank projection | Universal bank plane; shown to SDI but Basel outputs not regime-aware | `domain/forecasting/engine.py::project` via `regulatory_forecasting.py` | Bank facts, forecast assumptions, params | legacy parameter tables and forecast presets/defaults | Forecast run, capital plan, board/stress reporting | Yes for bank forecast | Year-0 overlap with capital/liquidity | **High CAR divergence; Medium policy gap** |
| Forecast | Case balance-sheet projection | Legacy case | `services/calculations.py::calculate_forecast` | `FinancialBalance`, `FinancialCashFlow`, `FinancialObligation`, case assumptions | manually reviewed case assumptions | Case forecast/liquidity/capital projections | Case-only | Separate formula, not filing authority | Medium naming/legacy |
| Stress | Enterprise macro stress | Bank & SDI branch | `domain/stress/orchestrator.py::run_enterprise_stress`, `services/enterprise_stress.py` | bank facts, scenarios, regulatory outputs/params | macro scenarios, management actions, class branch | Enterprise stress run, Appendix II/board pack | Yes for enterprise stress | Separate stress projection, intended scenario calculation | Medium hard-coded defaults |
| Stress | Reverse-stress frontier | Universal | `services/reverse_stress.py::run_reverse_stress` | bank facts and capital/liquidity engines | baseline thresholds/scenarios | Board pack / reverse stress UI | Yes | Calls existing capital/liquidity engines | Low |
| Reporting | LCR-NSFR package | Universal | `regulatory_reporting/generation.py::_generate_liquidity` | `regulatory_liquidity.get_bsd3_preview` from succeeded liquidity run | run parameter snapshot | package/export/filing | Yes | No independent ratio math; package totals tied to run | Low, test-proved |
| Reporting | CAR-RWA package | Universal | `_generate_capital` from `regulatory_capital.get_bsd2_preview` | succeeded capital run / line items | run parameter snapshot | package/export/filing | Yes | No independent headline ratio math in this generator | Low, subject to preview test coverage |
| Reporting | BoG workbook forms | Universal BSD | `bog_forms/engine.py::compute_form` + `formulas.py` | direct fact/position/reference resolvers plus selected run metrics | official workbook formulas + source resolvers | package/export/filing | Template authority | Independent template aggregation/formula calculation | **Medium: equivalence not uniformly proven** |
| Client | FTP business-line margin | UI only | `dashboard/components/ftp/businessLines.ts` | Backend FTP product figures | None | UI only | No | Presentational duplication: sum contribution / sum balance — labelled as a view aggregate and fail-closed since 2026-08-22 (§5.1) | Medium non-filing /
| Client | Charts/KPI deltas/ratios | UI only | dashboard pages/components | backend values | None | UI only | No | Presentational arithmetic | Low, except labeling/trend semantics |

## 4. Proven Calculation Lineage

### 4.1 Universal LCR

```
Canonical positions/reference rows
  -> fact_derivation.py creates BankFinancialFact
  -> regulatory_liquidity._load_facts
  -> domain/liquidity/engine.compute_lcr
  -> RegulatoryRun(metrics.lcr_pct, line items, input_hash)
  -> get_liquidity_dashboard / live metric
  -> LCR-NSFR package _generate_liquidity -> RegulatoryPackage.source_runs
  -> export / attestation / filing
```

Formula: `LCR = HQLA / (weighted outflows - min(weighted inflows, outflows x cap)) x 100`; decimal values quantized in the domain engine.

Evidence: `domain/liquidity/engine.py::compute_lcr`; `regulatory_liquidity.py::compute_live`; `regulatory_reporting/generation.py::_generate_liquidity`; `tests/api/test_regulatory_reporting.py::_assert_snapshot_binds_run`.

### 4.2 Universal CAR

```
Canonical inputs -> fact_derivation -> BankFinancialFact
  -> domain/capital/engine.compute_rwa
  -> domain/capital/engine.compute_capital_ratios
  -> RegulatoryRun(module=capital)
  -> capital dashboard / stress / CAR-RWA package
```

Formula: `CAR = total regulatory capital / total RWA x 100`; universal RWA includes credit, FX market, and BIA operational components.

### 4.3 SDI CAR

```
Canonical capital_structure references + canonical asset snapshots
  -> sdi_capital._net_own_funds + _exposure_by_bucket
  -> RegulatoryParameter resolver
  -> NOF / simplified RWA x 100
  -> SDI dashboard / exposure / SDI stress
```

This is not Basel CAR: legal input set, denominator and thresholds differ.

### 4.4 Case Cash Forecast / “Liquidity”

```
FinancialBalance + FinancialCashFlow + FinancialObligation + RiskScenario
  -> calculations.calculate_forecast
  -> CalculationForecastPeriod
  -> services/liquidity.calculate_metrics
  -> case RiskFinding
```

Formula: cash roll-forward with annualized revenue/expense growth, delay factor, first-period credit draw and scheduled repayment. Its minimum cash, source coverage, credit reliance and runway are not LCR/NSFR.

### 4.5 BoG Template Form

```
BankFinancialFact / canonical positions / references / selected RegulatoryRun metric
  -> named line-map resolver (facts.sum / positions.sum / run.metric / sources_ext)
  -> input cell values
  -> workbook formula evaluator
  -> GeneratedReturn(snapshot, bog_form cells)
  -> RegulatoryPackage/export
```

The template evaluator is deterministic. It does calculate official formula cells; it is not merely a format converter.

## 5. Confirmed Duplicate / Divergence Register

| Metric / concept | Implementation A | Implementation B | Same formula / inputs? | Can diverge? | Classification | Severity |
|---|---|---|---|---|---|---|
| Capital adequacy | Universal `compute_capital_ratios`: capital/RWA | SDI `NOF/simplified RWA` | No; different legal regimes | Yes, intended | Regime duplication | Medium |
| Liquidity | Universal LCR/NSFR | SDI Table 1/reserve/maturity | No; different legal regimes | Yes, intended | Regime duplication | Low |
| Capital / liquidity scenario analysis | Official runs | `analysis_workbench.run_analysis` | Yes for tested LCR/NSFR/CAR scenarios | Should not, test proves sampled equality | Safe scenario duplication | Low |
| Forecast year-0 CAR | `RegulatoryRun(capital)` | `RegulatoryRun(forecast).path[0]` | No: forecast fact scope excludes ECL exposure; capital may apply modeled ECL | **Yes, confirmed by test comment** | Dangerous semantic duplication | **High** |
| Forecast year-0 LCR/NSFR | Liquidity run | Forecast year-0 path | Yes for tested baseline | No under tested input set | Safe shared-engine duplication | Low |
| Case liquidity vs LCR | `services/liquidity.calculate_metrics` | `domain/liquidity.compute_lcr` | No | Yes, intended | Legacy semantic duplication | Medium |
| Case equity pressure vs CAR | `equity/assets` | capital `total_capital/RWA` | No | Yes, intended | Legacy semantic duplication | Medium |
| BoG capital/asset form cells | Direct fact / resolver aggregation + template formula | Capital engine result/line items | Partial/unknown per cell | Yes, not uniformly disproved | Reporting independent calculation | **Medium** |
| BoG LCR-NSFR package | liquidity run preview | package snapshot totals | Yes; explicit tests compare | No under tested package workflow | Safe reporting transformation | Low |
| FTP business-line margin | Backend product figures | client `sum contribution / sum balance` | Mathematically weighted aggregate but backend has no line grouping | Yes by grouping map | Presentational shadow calculation | Medium (non-filing) — **RESOLVED 2026-08-22, see §5.1** |
| Ratios / deltas in dashboards | backend metric | client formatting / delta / chart aggregation | Not authoritative | N/A to filing | Presentational duplication | Low |

### 5.1 Resolution note — FTP business-line margin (2026-08-22, WS-T)

Re-verified against the code: the backend still has **no business-line dimension
and no line-level margin**. `FtpProductRead` carries `product` / `category` /
`net_margin_pct`; `FtpBranchRead` carries a backend-computed
`ftp_adjusted_nim_pct` per BRANCH, which is a different dimension. There is no
endpoint to consume and no grouping map to import, so the client aggregate could
not be replaced by a backend figure.

It was therefore resolved as a **labelled presentational view**, not as an
alternate authority:

- `dashboard/components/ftp/businessLines.ts` states in its header that it is
  presentational only, may sum and divide engine figures and nothing more, and
  that no figure it produces may be filed, certified, or compared against a
  regulatory floor.
- The field is renamed `weightedMarginPct` → `impliedMarginPct` and widened to
  `number | null`. The previous `: 0` divide-guard was **fail-open**: a line with
  no balance rendered a real — and unusually good — 0% margin. It now renders
  "Not computable".
- `app/(app)/ftp/lines/page.tsx` carries the grouping rule, a margin-provenance
  notice (`MARGIN_NOTICE`), a "view aggregate" marker on the notice banner and on
  the Line P&L card, and the column reads "Implied margin (view)".
- `components/ftp/` was outside the fail-open guard's scan for the whole
  remediation programme, which is why this survived. The guard now scans every
  component tree that renders a regulatory or financial figure (35 → 123 → **219
  files**) and carries a rule, "§5 client-side ratio with a fabricated zero",
  that pins this exact shape. Reintroducing the original line fails it.

The classification stands: this remains a client-side aggregate with no engine
counterpart. What changed is that it can no longer be mistaken for one, and it
can no longer fabricate a measurement.

**Two further instances of the same fail-open shape** were found by the new rule
and fixed in the same change: `app/(app)/basel/planning/page.tsx` (pro-forma
CAR / Tier 1 / CET1 rendered `0.00%` when the base position had no RWA) and
`app/(app)/fx/var/page.tsx` (diversification benefit share and stressed-VaR
uplift multiple).

## 6. SDI vs Universal Matrix

| Metric | SDI | Universal bank | Shared engine? | Formula same? | Inputs same? | Parameters same? | Authority / risk |
|---|---|---|---|---|---|---|---|
| CAR | NOF / simplified RWA | total capital / credit+market+operational RWA | No | No | No | No | Intended legal divergence |
| CET1/Tier 1/Leverage | Not applicable in s.29 view | Basel capital engine | No | N/A | N/A | N/A | Universal only |
| LCR/NSFR | Not calculated as SDI compliance | Basel liquidity engine | No | N/A | N/A | N/A | Universal only |
| LMTD Table 1 | Binding thresholds | Monitoring formula / class floor where requested | Shared helper/resolver | Yes | Canonical positions | Class-specific thresholds | Correct class divergence |
| Reserves | Primary/secondary reserve | HQLA / LCR inputs | No | No | Partially | No | Different regimes |
| Maturity ladder / concentration / capacity | Direct canonical helper | Shared monitoring endpoint | Same helper | Yes | Yes | Haircut/threshold policy differs | Shared operating metric |
| Loan classification | NBFI 4-grade | Bank 5-grade | Shared class-aware engine | No | Same canonical loan fields | class provisioning grid | Intended |
| ECL | Same ECL domain engine when enabled | Same | Yes | Yes | Same exposure/assumption model | Same ECL assumptions | Shared |
| Enterprise stress capital | SDI s.29 branch | CRD Basel branch | Same orchestration, branch-specific builders | No | Different fact/parameter constructs | No | Intended regime divergence |
| Forecast | Currently bank-style Basel ratios shown to SDI | Bank forecast | Same bank engine | Same currently, but inappropriate for SDI control use | Bank facts | Basel params | **Open product/architecture gap** |

## 7. Policy / Jurisdiction Audit

### Verified resolution chain

- `Bank.jurisdiction_code` -> `jurisdictions.base_currency(bank)` for bank currency (no fallback).
- `Bank.institution_type` -> `institution_types.get_type` -> class, return family, capital regime, module set.
- SDI regulatory values -> `regulatory_parameters.resolve` precedence: institution type -> class, jurisdiction + effective date.
- Legacy universal `Param*` tables -> `params.get_active_params(organization, jurisdiction, as_of)`.

### Confirmed policy issues

| Location | Behavior | Can affect official result? | Risk |
|---|---|---|---|
| `models/regulatory_parameter.py::RegulatoryParameter.jurisdiction_code` | default `GH` | Yes, if a row is created without an explicit jurisdiction | High regional-expansion risk |
| `models/regulatory.py::RegulatoryParameterMixin.jurisdiction_code` | default `GH` for legacy Param* tables | Yes | High regional-expansion risk |
| `models/temenos.py::TemenosConnection.default_currency` | default `GHS` | Indirectly, through ingestion mapping | High data-conversion risk |
| `regulatory_parameters.try_resolve` | blank jurisdiction falls back `GH`; unknown institution type falls back universal type | Yes under corrupted/incomplete bank data | Medium |
| `fact_derivation.py` | default CCF / NMD / HQLA classifications in some missing-data paths | Yes | High data-quality risk |
| `enterprise_stress.py` and `domain/stress/translation.py` | code defaults for baseline assumptions / stress elasticities | Yes for enterprise stress / Appendix II | High policy-governance risk |
| `jurisdictions.base_currency` | no default | Yes, but fails loudly | Correct behavior |

## 8. Reporting Audit

### Correctly run-backed

- `LCR-NSFR` package via `_generate_liquidity` uses `regulatory_liquidity.get_bsd3_preview`, requires baseline run, copies preview totals, and records source runs.
- `CAR-RWA` package via `_generate_capital` uses the capital preview, obtains run-backed line items/metrics, and records source runs.
- Stress pack uses run metrics from capital/liquidity/forecast/reverse/enterprise stress and records source run lineage.

### Independently calculated from reporting sources

- `bog_form` returns call `compute_form`, which resolves direct fact/position/reference sources into template input cells and evaluates official workbook formulas. It returns `source_runs=[]`.
- BSD5 sources mix direct capital fact aggregation and selected capital-run lines.
- BSD2A calculates ratios over BSD2 dependency cells with `numerator / denominator * 100`.

### Audit verdict on reporting

The package-level LCR-NSFR tie-back is proven. It is **not proven** that every BoG template ratio or total which overlaps an engine has an automated equivalence test against that engine. The template formula is likely the official source of truth for that return, but the architecture has no universal “metric authority registry” to make this explicit or fail when an engine/template mapping diverges.

## 9. Architectural Verdict Answers

1. **One authoritative pipeline?** No.
2. **How many planes?** Four material executable planes listed in section 2; the scenario workbench is a safe non-persistent execution mode of the bank plane, not a fifth independent formula plane.
3. **Which metrics are duplicated?** Section 5.
4. **SDI and universal same engines?** Only for class-neutral functions (ECL, class-aware loan classification, some shared canonical monitoring helpers). Capital/liquidity compliance engines differ by regime.
5. **Does reporting consume operational results?** For LCR-NSFR, CAR-RWA, stress packs: yes. For BoG template forms: partly; direct source/resolver/template calculation remains independent.
6. **Can same facts produce different official results depending on API/workspace?** Case workspace cannot feed official result in verified paths. Within bank plane, forecast Year-0 CAR can differ from capital CAR. Template vs engine equivalence across every overlapping form field is unknown.
7. **Does case workspace calculate something that belongs to canonical financial domain?** It independently models balances/cash/debt, but only in a case workflow. It must not be treated as a bank financial system of record.
8. **Frontend/reporting/service bypasses?** Frontend does only presentation aggregates except FTP business-line margin (resolved 2026-08-22 as a labelled presentational view — §5.1 — since the backend has no line dimension to consume). Reporting templates calculate from sources independently. Services contain code defaults that bypass policy registers in selected paths.
9. **Policy defaults capable of changing official results?** Yes: GH/GHS model defaults, fact defaults, stress assumptions/elasticities, and fallback type/jurisdiction behavior.
10. **What must consolidate?** Policy resolution, metric authority/equivalence controls, and boundaries between case and bank planes; do not merge legally distinct SDI/Bank engines.

## 10. Required Remediation

### Immediate controls

1. Add a machine-readable metric authority registry: metric, scope, canonical inputs, policy resolver, engine, output run type, reporting field maps, tolerance, and forbidden alternative sources.
2. Add cross-engine equivalence tests: forecast Y0 vs capital/liquidity; every run-backed report metric vs source run; each overlapping BoG form/engine metric vs declared tolerance.
3. Make report packages include explicit provenance for direct fact/template calculations: source period/fact generation, parameter versions, mapping version, template hash, formula evaluator version.
4. Replace calculation-affecting GH/GHS/defaults with mandatory jurisdiction/regime resolution or named governed exceptions.

### Case plane boundary

- Keep `risk_cases` only as advisory credit/case analysis.
- Rename case liquidity/capital outputs to `cash-flow adequacy` and `solvency pressure`.
- Add automated import/dependency guards preventing `regulatory_reporting`, `regulatory_*`, and bank official-run services from importing `CalculationRun`, `CalculationForecastPeriod`, `CapitalProjection`, or `financial_*` models.
- Do not migrate case output into `BankFinancialFact` without a reviewed canonical adapter and reconciliation.

### SDI / universal boundary

- Keep separate legal-regime engines; centralize only shared input normalization and policy resolution.
- Make the forecast engine regime-aware or explicitly block/label Basel forecast ratios for SDI tenants.
- Replace simplified SDI hard-coded bucket mapping with governed policy data before broader SDI product scope.

### Migration sequence

1. Inventory + authority registry; no behavior change.
2. Add dependency and equivalence tests; label legacy case outputs.
3. Centralize defaults/parameters with effective-dated versioned governance.
4. Add reporting provenance/equivalence gates.
5. Decide case product retention/migration/decommission based on supported customer workflow.

## 11. Evidence Limits

- This pass did not prove every formula in all BoG workbooks against every domain engine; it traced the framework and material package routes, and identifies remaining equivalence work as required.
- It did not execute live primary-database calculations; test/code evidence is authoritative only for covered paths.
- It did not assess external ORASS arithmetic.
- No claim of one authoritative financial pipeline is supported by current code.
