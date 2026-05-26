# AequorOS MVP Implementation Approach

## Purpose

This document defines the implementation approach to move AequorOS from a static-data prototype to a database-driven, multi-tenant-ready MVP that can be tested with a live bank. It is written for engineering execution, with emphasis on calculation logic correctness, reproducibility, and regulatory traceability.

This approach assumes:

- You own calculation logic.
- We will provision one bank first.
- Architecture must support multi-tenant expansion without redesign.
- Frontend should fetch computed outputs and drilldowns from APIs, not hardcoded datasets.

---

## 1. Target State

### 1.1 Business outcome

- Demo and evaluate one provisioned bank using realistic data.
- Ensure liquidity, capital, and forecasting numbers are computed from database records.
- Preserve ability to onboard additional banks with tenant isolation.

### 1.2 Technical outcome

- Server-side calculation service for all regulatory and forecasting metrics.
- Canonical database model with effective-dated regulatory parameters.
- Calculation runs persisted for audit, replay, and validation.
- UI consumes API outputs only.

---

## 2. Design Principles

1. Single source of truth: all module inputs come from canonical DB tables.
2. Pure calculation core: deterministic functions with no external side effects.
3. Reproducibility: same input snapshot + same parameter version = same output.
4. Explainability: every KPI is backed by line-item drilldown.
5. Isolation by default: tenant scoping in schema, query layer, and authorization.
6. Config over code: runoff rates, ASF/RSF weights, risk weights, thresholds, and scenario shocks live in data tables.

---

## 3. Scope for This Implementation

### In scope

- Liquidity calculation engine (LCR, NSFR, stress recalculation).
- Basel calculation engine (RWA components, CET1/Tier1/CAR/Leverage, stress).
- Forecasting engine (projection, scenario runs, constrained optimizer scoring).
- Data ingestion path for one bank dataset.
- Multi-tenant foundations (tenant_id and isolation controls).
- API contracts for UI integration.

### Out of scope for this phase

- Deep RL optimizer training (use constrained scenario optimization).
- Core banking integration (Temenos/T24 direct connectors).
- Full production IAM and enterprise-grade security stack.
- Real regulatory filing integration (preview generation only).

---

## 4. High-Level Architecture

1. Frontend (Next.js): requests KPI cards, charts, tables, and submission previews from APIs.
2. API layer: validates tenant and context (bank_id, as_of_date, scenario_id).
3. Calculation service: executes deterministic modules and writes run outputs.
4. PostgreSQL: stores source facts, assumptions, parameter versions, and run results.
5. Optional worker queue: handles asynchronous heavy calculations (stress and long-horizon forecast).

Suggested runtime pattern:

- Synchronous for fast metrics (target under 2 seconds).
- Async job for heavy runs (target under 5 seconds for completion, polled via run_id).

---

## 5. Data Model Blueprint (Canonical)

All business tables must include:

- tenant_id
- bank_id
- as_of_date or effective_from/effective_to
- created_at, updated_at
- source_system (when applicable)

### 5.1 Core dimensions

- tenant
- bank
- branch
- product
- counterparty_type
- currency
- regulatory_jurisdiction
- scenario

### 5.2 Position and balance fact tables

- fact_balance_sheet_position
  - category (cash, securities, loans, other_assets, deposits, borrowings, capital)
  - amount_local
  - currency
- fact_loan_exposure
  - segment (retail, SME, corporate, mortgage)
  - ead
  - pd
  - lgd
  - risk_weight_code
- fact_securities_holding
  - security_type (BoG bill, GoG security)
  - carrying_amount
  - hqla_level
  - risk_weight_code
- fact_deposit_behavior
  - segment (stable, less_stable, operational, non_operational)
  - balance
  - core_vs_volatile_flag

### 5.3 Liquidity-specific facts

- fact_lcr_outflow_base
  - category
  - exposure_amount
- fact_lcr_inflow_base
  - category
  - exposure_amount
- fact_nsfr_asf_base
  - category
  - amount
- fact_nsfr_rsf_base
  - category
  - amount

### 5.4 Capital-specific facts

- fact_capital_components
  - cet1_component, at1_component, tier2_component, deduction_component
  - amount
- fact_market_risk_position
  - net_long_fx
  - net_short_fx
- fact_operational_risk_income
  - year
  - gross_income

### 5.5 Forecasting facts

- fact_historical_daily_cashflows
  - date
  - inflow
  - outflow
  - ending_balance
- fact_macro_assumption
  - variable (gdp, inflation, fx, policy_rate)
  - value
- fact_projection_input
  - product/segment-level starting balances, rates, growth assumptions

### 5.6 Effective-dated parameter tables

- param_lcr_runoff_rate
- param_lcr_inflow_cap
- param_nsfr_weight
- param_risk_weight
- param_capital_threshold
- param_stress_shock

All parameter rows should be versioned with:

- parameter_set_id
- effective_from
- effective_to
- jurisdiction_code
- approved_by
- approval_timestamp

---

## 6. Calculation Service Design

### 6.1 Execution context

Each run requires:

- tenant_id
- bank_id
- as_of_date
- module (liquidity | basel | forecasting)
- scenario_id (optional for baseline)
- parameter_set_id

### 6.2 Run persistence

Create run-tracking tables:

- calc_run
  - run_id, module, status, started_at, completed_at, context_json
- calc_metric_result
  - run_id, metric_code, metric_value, unit, threshold_min, threshold_warn, status
- calc_line_item
  - run_id, metric_code, line_code, line_description, amount, formula_ref
- calc_validation_result
  - run_id, rule_code, result, message

### 6.3 Deterministic module functions

Implement pure functions:

- computeLCR(inputs, params)
- computeNSFR(inputs, params)
- computeRWA(inputs, params)
- computeCapitalRatios(inputs, params)
- runStressScenario(inputs, scenarioParams)
- runForecast(inputs, assumptions, horizon)
- rankOptimizationCandidates(candidates, constraints)

Function outputs should include both:

- headline metrics
- full decomposition used for tables and regulatory preview sections

### 6.4 Formula references to encode

- LCR = HQLA / NetCashOutflow30d, with inflow cap at 75 percent of outflows.
- NSFR = ASF / RSF.
- Credit RWA = sum(EAD x risk_weight).
- Market Risk charge (FX) = 8 percent of max(net_long, net_short).
- Operational Risk charge = 15 percent of positive average gross income (3 years).
- CET1 ratio = CET1 / TotalRWA.
- Tier1 ratio = (CET1 + AT1) / TotalRWA.
- CAR = (Tier1 + Tier2) / TotalRWA.
- Leverage ratio = Tier1 / TotalExposure.

---

## 7. API Contract Pattern

### 7.1 Trigger or fetch baseline run

- POST /api/v1/calculations/{module}/run
  - body: tenant_id, bank_id, as_of_date, scenario_id, parameter_set_id
  - response: run_id, status

### 7.2 Fetch run summary

- GET /api/v1/calculations/runs/{run_id}
  - response: status, metric cards, warnings, validation summary

### 7.3 Fetch drilldown table

- GET /api/v1/calculations/runs/{run_id}/metrics/{metric_code}/lines
  - response: line items and formula references

### 7.4 Fetch submission preview payload

- GET /api/v1/submissions/{module}/preview?run_id=...
  - response: structured payload mapped to BoG layout sections

---

## 8. Migration Plan From Current Prototype

Current prototype stores synthetic data in TypeScript files under app code. Migration should be incremental.

### Phase A: Introduce backend boundaries

1. Add API routes and response types.
2. Keep existing static data as temporary source behind service interfaces.
3. Refactor frontend pages to consume API contracts instead of importing local datasets directly.

### Phase B: Database integration

1. Create canonical schema and seed one bank dataset.
2. Switch service implementations from static dataset provider to Postgres provider.
3. Validate parity between old and new outputs for selected as_of_date values.

### Phase C: Run persistence and stress/forecast jobs

1. Add calc_run and result tables.
2. Persist every baseline and stressed run.
3. Add async processing where needed.

### Phase D: Hardening for live bank evaluation

1. Add data quality checks and reject invalid loads.
2. Add parameter set approval workflow.
3. Add regression test suite with golden expected results.

---

## 9. One-Bank Provisioning Plan

### 9.1 Bank onboarding package

Prepare a versioned seed package containing:

- bank profile and org structure
- balance sheet opening positions
- loan segmentation and PD/LGD assumptions
- securities and HQLA mapping
- deposit behavioral segmentation
- historical daily cashflow series (minimum 24 months)
- base/adverse/severe scenario assumptions

### 9.2 Reconciliation gates

Before enabling UI:

1. Balance sheet totals tie exactly: assets = liabilities + equity.
2. Liquidity inputs reconcile to LCR/NSFR line items.
3. Basel exposures reconcile to RWA decomposition.
4. Forecast opening balances reconcile to current balance sheet.

### 9.3 Demo-readiness gates

1. All key KPI runs complete under target times.
2. Drilldowns available for every headline metric.
3. Stress scenarios produce plausible directional movement.
4. Submission previews render with validation flags.

---

## 10. Testing Strategy For Calculation Logic

### 10.1 Test layers

1. Unit tests for each formula function.
2. Module integration tests for complete liquidity/basel/forecast runs.
3. End-to-end API tests for run initiation and result retrieval.
4. Golden tests: hand-calculated expected outputs for selected cases.

### 10.2 Minimum test pack

- Baseline case (normal conditions).
- Threshold edge cases (for green/amber/red status transitions).
- Stress cases (idiosyncratic, market-wide, combined).
- Data anomaly cases (missing category, negative balance where invalid, stale parameter set).

### 10.3 Non-negotiable assertions

- No metric can be returned without traceable line items.
- Any missing parameter version must fail the run clearly.
- Same run context must produce identical outputs.

---

## 11. Delivery Timeline (Execution-Focused)

### Week 1

- Finalize canonical schema and parameter model.
- Define API contracts and calculation module boundaries.
- Create seed scripts for one bank.

### Week 2

- Implement liquidity calculation service with run persistence.
- Add tests for LCR and NSFR baseline and stress.
- Integrate liquidity screens with API.

### Week 3

- Implement basel calculation service with run persistence.
- Add RWA and capital ratio tests.
- Integrate basel screens with API.

### Week 4

- Implement forecasting service and constrained optimizer ranking.
- Integrate scenario and what-if endpoints.
- Add cross-module reconciliation checks.

### Week 5

- Submission preview payload generation.
- Performance tuning and data quality gates.
- Full demo dry-runs and defect fixes.

---

## 12. Work You Can Start Before Database URL Is Ready

1. Define calculation domain types and interfaces.
2. Implement pure formula functions and test packs.
3. Build API contracts and mock provider.
4. Prepare SQL migrations and seed file templates.
5. Add reconciliation utility functions.

This allows immediate progress while connection details are pending.

---

## 13. Once Database URL Is Available: Activation Checklist

1. Apply schema migrations.
2. Load parameter sets and one-bank seed data.
3. Run reconciliation scripts and fix mismatches.
4. Execute baseline calculation runs and compare against expected results.
5. Enable UI integration for liquidity, basel, and forecasting.
6. Execute stress scenarios and validate outputs.
7. Freeze a demo snapshot for consistent stakeholder walkthroughs.

---

## 14. Risks and Mitigations

### Risk: hardcoded fallback logic remains in frontend

Mitigation: enforce API-only data access for KPI and module views.

### Risk: parameter drift causes silent metric shifts

Mitigation: effective-dated parameter sets with approval metadata and run-time capture of parameter_set_id.

### Risk: cross-module inconsistencies undermine credibility

Mitigation: mandatory reconciliation gate before each demo release.

### Risk: ambiguous stress assumptions

Mitigation: scenario table with explicit shock values and versioning.

---

## 15. Definition of Done

Implementation is complete when:

1. All three modules compute from database records only.
2. Every KPI has stored run metadata and line-item traceability.
3. Baseline and stress outputs pass golden test assertions.
4. Frontend has no embedded regulatory formulas or static metric constants.
5. One bank tenant is fully provisioned and demo-ready.

---

## 16. Handoff and Continuation Protocol

This section ensures implementation can continue smoothly when work moves from the calculation owner to another developer.

### 16.1 Required handoff package

Before handoff, provide all of the following in the repository:

1. Updated implementation status note (what is done, in progress, blocked).
2. Current schema migration state and latest migration identifier.
3. Current parameter_set_id in use and parameter change history.
4. Golden test cases with expected values and tolerance rules.
5. Sample run IDs for baseline and stress scenarios with known-good outputs.
6. Open defect list with severity and reproduction steps.
7. Environment variable checklist (without secrets committed to git).

### 16.2 Minimum artifacts to attach in handoff

1. API contract examples (request and response JSON for each endpoint).
2. Reconciliation report for latest seed data load.
3. Test run summary (unit, integration, end-to-end) with pass/fail counts.
4. Runbook for calculation execution and troubleshooting.

### 16.3 Continuation rules for the next developer

1. Do not change formulas and parameter logic in the same pull request.
2. Any formula change requires a golden test update and a new formula reference version.
3. Any parameter change requires effective dates and approval metadata.
4. No frontend page should consume hardcoded metric constants.
5. Any new metric card must include drilldown line items and validation rules.

### 16.4 Branch and release workflow

1. Use short-lived feature branches per module area (liquidity, basel, forecasting, platform).
2. Merge only after calculation parity checks pass against known baseline runs.
3. Tag demo-stable snapshots so stakeholder walkthroughs always use reproducible data and outputs.

### 16.5 Go/No-Go checks for developer transition

Handoff is Go only when:

1. Database migrations run cleanly in a fresh environment.
2. Seed load completes with no unresolved reconciliation exceptions.
3. Baseline and stress runs execute and match expected golden outputs.
4. The new developer can run a full module flow locally from API to UI.

If any check fails, handoff is No-Go and must be remediated before transition.

---

## Appendix A: Suggested Table Names

- tenant
- bank
- scenario
- parameter_set
- param_lcr_runoff_rate
- param_nsfr_weight
- param_risk_weight
- param_stress_shock
- fact_balance_sheet_position
- fact_loan_exposure
- fact_securities_holding
- fact_deposit_behavior
- fact_historical_daily_cashflows
- fact_capital_components
- fact_market_risk_position
- fact_operational_risk_income
- calc_run
- calc_metric_result
- calc_line_item
- calc_validation_result

---

## Appendix B: Ownership Split

- Calculation Owner (you):
  - formula implementation
  - scenario logic
  - validation rules
  - golden expected outputs
- Data/Platform Owner:
  - schema migrations
  - ETL and seed loading
  - run orchestration
  - API and environment setup
- Frontend Owner:
  - API integration
  - drilldown rendering
  - submission preview presentation
