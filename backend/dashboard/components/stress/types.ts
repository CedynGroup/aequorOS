/**
 * Hand-written contracts for the stress workbench (docs/stress.md Phase 6).
 *
 * These mirror the backend Pydantic schemas EXACTLY as they serialise on the
 * wire (app/schemas/{stress,enterprise_stress,management_actions}.py and the
 * domain serializers under app/domain/stress/). Because the workbench fetches
 * these endpoints directly (the generated OpenAPI client does not yet carry the
 * Phase-1..5 stress surface, and regeneration is owned by a concurrent effort),
 * the JSON is consumed raw — hence **snake_case** keys and stringified Decimals,
 * matching the FastAPI response bodies.
 */

import type { ScenarioStatus, ScenarioType, Severity } from './macro';

// --- Macro scenarios (schemas/stress.py) -------------------------------------

export type MacroPath = {
  variable: string;
  year_index: number;
  base_value: string;
  stress_value: string;
};

export type MacroScenarioSummary = {
  id: string;
  bank_id: string | null;
  code: string;
  name: string;
  scenario_type: ScenarioType;
  severity: Severity | null;
  horizon_years: number;
  status: ScenarioStatus;
  version: number;
  path_count: number;
  created_by: string | null;
  approved_by: string | null;
  created_at: string;
  updated_at: string;
};

export type MacroScenarioList = {
  scenarios: MacroScenarioSummary[];
  total: number;
};

export type MacroScenario = {
  id: string;
  organization_id: string;
  bank_id: string | null;
  code: string;
  name: string;
  description: string | null;
  scenario_type: ScenarioType;
  severity: Severity | null;
  horizon_years: number;
  narrative: string | null;
  source: string | null;
  status: ScenarioStatus;
  version: number;
  created_by: string | null;
  approved_by: string | null;
  approval_timestamp: string | null;
  institution_type_applicability: string[] | null;
  paths: MacroPath[];
  created_at: string;
  updated_at: string;
};

export type MacroPathIn = {
  variable: string;
  year_index: number;
  base_value: string;
  stress_value: string;
};

export type MacroScenarioCreate = {
  code: string;
  name: string;
  description?: string | null;
  scenario_type: ScenarioType;
  severity?: Severity | null;
  horizon_years: number;
  narrative?: string | null;
  source?: string | null;
  bank_id?: string | null;
  institution_type_applicability?: string[] | null;
  paths: MacroPathIn[];
  reason: string;
};

// --- Management-action plans (schemas/management_actions.py) ------------------

export type PlanStatus = 'draft' | 'pending_approval' | 'approved' | 'archived';

export type ActionItem = {
  action_id: string;
  kind: string;
  label: string;
  sort_order: number;
  trigger_kind: string;
  watch_minima: string[] | null;
  min_severity: string | null;
  effective_year: number;
  capital_raise_ghs: string;
  capital_raise_tier: string;
  counts_as_paid_up: boolean;
  sizing: string;
  dividend_reduction_pct: string;
  rwa_reduction_ghs: string;
  shrinks_leverage_exposure: boolean;
  severity_factors: Record<string, string> | null;
  rationale: string | null;
};

export type ManagementActionPlanSummary = {
  id: string;
  bank_id: string | null;
  code: string;
  name: string;
  status: PlanStatus;
  version: number;
  action_count: number;
  created_by: string | null;
  approved_by: string | null;
  created_at: string;
  updated_at: string;
};

export type ManagementActionPlanList = {
  plans: ManagementActionPlanSummary[];
  total: number;
};

export type ManagementActionPlan = {
  id: string;
  organization_id: string;
  bank_id: string | null;
  code: string;
  name: string;
  description: string | null;
  status: PlanStatus;
  version: number;
  created_by: string | null;
  approved_by: string | null;
  approval_timestamp: string | null;
  actions: ActionItem[];
  created_at: string;
  updated_at: string;
};

// --- Enterprise stress run (schemas/enterprise_stress.py + domain serializers)

export type EnterpriseStressSummary = {
  scenario_code: string;
  stressed_car_end_pct: string;
  baseline_car_end_pct: string;
  car_erosion_pp: string;
  // Basel LCR/NSFR + the solvency–liquidity coupling: null for an SDI run
  // (docs/sdi.md §4.6). Treat null as "not assessed under the SDI regime".
  stressed_lcr_pct: string | null;
  baseline_lcr_pct: string | null;
  both_breached: boolean | null;
  stress_stays_above_all_minima: boolean;
  first_breach_year: number | null;
  binding_minima: string[];
  capital_gap: string;
  management_action_plan_code: string | null;
  with_actions_stays_above_all_minima: boolean | null;
  with_actions_first_breach_year: number | null;
  residual_capital_required_after_actions: string | null;
};

/** A lightweight run-history row (backend `EnterpriseStressRunSummary`, GET …/runs). */
export type EnterpriseStressRunSummary = {
  run_id: string;
  reporting_period_id: string;
  scenario_code: string;
  status: string;
  input_hash: string;
  engine_version: string;
  stressed_car_end_pct: string;
  baseline_car_end_pct: string;
  car_erosion_pp: string;
  stress_stays_above_all_minima: boolean;
  first_breach_year: number | null;
  capital_gap: string;
  management_action_plan_code: string | null;
  with_actions_stays_above_all_minima: boolean | null;
  created_at: string;
};

/** One projected year in `projection.{current,base[],stress[]}` (service `_serialize_projection`). */
export type ProjectionYear = {
  year: number;
  leg: 'current' | 'base' | 'stress';
  car_pct: string;
  cet1_ratio_pct: string;
  tier1_ratio_pct: string;
  leverage_ratio_pct: string;
  lcr_pct: string;
  nsfr_pct: string;
  net_income: string;
  credit_losses: string;
  pd_multiplier: string;
  lgd_multiplier: string;
  minima_all_ok: boolean;
  binding_minima: string[];
};

export type EnterpriseProjection = {
  scenario_code: string;
  horizon_years: number;
  stress_stays_above_all_minima: boolean;
  first_breach_year: number | null;
  binding_minima: string[];
  current: ProjectionYear;
  base: ProjectionYear[];
  stress: ProjectionYear[];
};

// Appendix II (domain/stress/appendix_ii.py::_serialize_tables). Amounts GHS'000.

export type CapitalSnapshot = {
  label: string;
  cet1: string | null;
  tier1: string | null;
  tier2: string | null;
  total_regulatory_capital: string | null;
  total_rwa: string | null;
  cet1_ratio_pct: string | null;
  tier1_ratio_pct: string | null;
  car_pct: string | null;
  paid_up: string | null;
};

export type ExposureClassLoss = { exposure_class: string; loss: string | null };

export type ManagementActionsRow = {
  year: number;
  capital_raised_cet1: string | null;
  capital_raised_at1: string | null;
  capital_raised_tier2: string | null;
  capital_raised_total: string | null;
  revision_of_dividend_policy: string | null;
  change_in_business_strategy: string | null;
  sale_of_assets: string | null;
  risk_reduction: string | null;
  other: string | null;
  total_management_actions: string | null;
  rwa_relief_total: string | null;
};

export type Table1ManagementActions = {
  plan_id: string;
  stays_above_all_minima: boolean;
  first_breach_year: number | null;
  binding_minima: string[];
  rows: ManagementActionsRow[];
};

export type Table1Summary = {
  car_target_pct: string | null;
  paid_up_min: string | null;
  current: CapitalSnapshot;
  pre_adverse: CapitalSnapshot[];
  post_adverse: CapitalSnapshot[];
  impact_of_adverse: { year: number; losses: ExposureClassLoss[] }[];
  capital_required_car_target: { year: number; amount: string | null }[];
  capital_required_paid_up: { year: number; amount: string | null }[];
  capital_gap: string | null;
  management_actions: Table1ManagementActions | null;
  post_capitalisation: CapitalSnapshot[] | null;
  residual_capital_required_after_actions:
    | { worst: string | null; rows: { year: number; residual_capital_required: string | null }[] }
    | null;
};

export type Table2CetBuild = {
  paid_up: string | null;
  retained_earnings: string | null;
  statutory_reserves: string | null;
  other_reserves: string | null;
  minority_interest: string | null;
  other_cet1: string | null;
  gross_cet1: string | null;
  deduction_intangibles: string | null;
  deduction_fi_investments: string | null;
  deduction_oci: string | null;
  deduction_dta: string | null;
  deduction_commercial_entity: string | null;
  deduction_other: string | null;
  total_deductions: string | null;
  cet1_after_deductions: string | null;
};

export type Table2Row = {
  label: string;
  total_rwa: string | null;
  cet1: Table2CetBuild;
  at1_nominal: string | null;
  at1_cap: string | null;
  at1_eligible: string | null;
  tier2_nominal: string | null;
  tier2_cap: string | null;
  tier2_eligible: string | null;
  total_regulatory_capital: string | null;
  credit_risk_reserve: string | null;
};

export type Table3Row = {
  label: string;
  opening_retained_earnings: string | null;
  net_interest_income: string | null;
  fees_and_commissions: string | null;
  trading_income: string | null;
  other_income: string | null;
  operating_expenses: string | null;
  impairment_losses: string | null;
  depreciation_amortisation: string | null;
  profit_before_tax: string | null;
  tax: string | null;
  profit_after_tax: string | null;
  distributions: string | null;
  adjusted_retained_earnings_for_car: string | null;
};

export type Table4Row = {
  label: string;
  cash_and_balances: string | null;
  short_term_investments: string | null;
  loans: string | null;
  other_assets: string | null;
  total_assets: string | null;
  demand_deposits: string | null;
  savings_deposits: string | null;
  time_deposits: string | null;
  other_deposits: string | null;
  borrowings: string | null;
  total_liabilities: string | null;
  capital: string | null;
};

export type Pillar2 = {
  credit_concentration: string | null;
  irrbb: string | null;
  sovereign: string | null;
  country_and_fx: string | null;
  reputational: string | null;
  other: string | null;
  total: string | null;
};

export type Table5Row = {
  label: string;
  credit_rwa: string | null;
  operational_rwa: string | null;
  market_rwa: string | null;
  total_pillar1_rwa: string | null;
  pillar1_requirement: string | null;
  pillar2: Pillar2;
  total_capital_requirement: string | null;
};

export type Table6Row = {
  variable: string;
  year_index: number;
  base_value: string | null;
  stress_value: string | null;
};

export type AppendixIITables = {
  scenario_code: string;
  horizon_years: number;
  unit: string;
  table1_summary: Table1Summary;
  table2_capital: Table2Row[];
  table3_profit_and_loss: Table3Row[];
  table4_financial_position: Table4Row[];
  table5_rwa: { car_target_pct: string | null; rows: Table5Row[] };
  table6_risk_drivers: { source: string | null; rows: Table6Row[] };
};

/** The enterprise-stress outcome (orchestrator `_serialize_outcome`) — partial. */
export type EnterpriseOutcome = {
  scenario_code: string;
  engine_version: string;
  capital: Record<string, unknown> & {
    baseline_car_end_pct: string;
    stressed_car_end_pct: string;
    car_erosion_pp: string;
    annual_incremental_credit_loss?: string;
    ecl_base?: string;
    ecl_stress?: string;
    pd_multiplier?: string;
    lgd_multiplier?: string;
  };
  // A bank run carries the Basel LCR/NSFR block; an SDI run carries the
  // not-assessed marker ({assessed:false, regime, reason}) — docs/sdi.md §4.6.
  liquidity:
    | (Record<string, unknown> & {
        baseline_lcr_pct: string;
        stressed_lcr_pct: string;
        fx_depreciation_pct: string;
      })
    | { assessed: false; regime: string; reason: string };
  // Absent on an SDI run (no Basel solvency–liquidity coupling).
  coupling?: {
    stressed_car_end_pct: string;
    car_min_pct: string;
    car_breached: boolean;
    stressed_lcr_pct: string;
    lcr_min_pct: string;
    lcr_breached: boolean;
    both_breached: boolean;
    narrative: string;
  };
  irr?: {
    base_eve: string;
    stressed_eve: string;
    delta_eve: string;
    delta_eve_pct_tier1: string;
    parallel_bp: string;
    delta_nii: string;
  };
  fx?: {
    base_nop_pct_tier1: string;
    stressed_nop_pct_tier1: string;
    shock_pct: string;
    stressed_within_aggregate_limit: boolean;
  };
  concentration?: Record<string, unknown>;
  operational?: Record<string, unknown>;
  contingent_leverage?: Record<string, unknown>;
};

export type EnterpriseStressRead = {
  run_id: string;
  bank_id: string;
  reporting_period_id: string;
  scenario_id: string;
  scenario_code: string;
  input_hash: string;
  engine_version: string;
  summary: EnterpriseStressSummary;
  outcome: EnterpriseOutcome;
  projection: EnterpriseProjection;
  appendix_ii: AppendixIITables;
  created_at: string;
};

export type EnterpriseStressRunCreate = {
  scenario_id: string;
  reporting_period_id: string;
  plan?: Record<string, string> | null;
  management_action_plan_id?: string | null;
  horizon_years?: number;
  paid_up_min?: string | null;
  car_target_pct?: string;
  include_irr?: boolean;
  include_fx?: boolean;
  reason: string;
};

// --- Enterprise-stress sign-off / Board attestation (backend EnterpriseStressSignoff) ---
export type StressSignoffStatus = 'draft' | 'pending_attestation' | 'attested' | 'withdrawn';

export type StressSignoffSummary = {
  id: string;
  run_id: string;
  reporting_period_id: string;
  scenario_code: string;
  status: StressSignoffStatus;
  version: number;
  stays_above_all_minima: boolean | null;
};

export type StressSignoffListRead = {
  signoffs: StressSignoffSummary[];
  total: number;
};

export type StressSignoffRead = {
  id: string;
  organization_id: string;
  bank_id: string;
  run_id: string;
  reporting_period_id: string;
  scenario_code: string;
  status: StressSignoffStatus;
  version: number;
  scenario_narrative: string;
  assumptions_rationale: string;
  methodology_summary: string | null;
  board_challenge: string | null;
  credibility_rationale: string | null;
  stays_above_all_minima: boolean | null;
  with_actions_stays_above_all_minima: boolean | null;
  created_by: string | null;
  submitted_by: string | null;
  submitted_at: string | null;
  attested_by: string | null;
  attested_at: string | null;
  created_at: string;
  updated_at: string;
};

export type StressSignoffCreate = {
  run_id: string;
  scenario_narrative: string;
  assumptions_rationale: string;
  methodology_summary?: string | null;
  reason: string;
};

export type StressSignoffAttestation = {
  credibility_rationale: string;
  board_challenge?: string | null;
  reason: string;
};
