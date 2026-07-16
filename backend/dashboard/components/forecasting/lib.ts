/**
 * Shared vocabulary for the Balance Sheet Forecasting workspace.
 *
 * Everything here mirrors the real API contract in
 * packages/risk-service-api (ForecastRunRead / ResolvedForecastAssumptions /
 * ProjectionYearRead) — no invented fields. Display helpers derive values
 * only from persisted run payloads and label the derivation where they do.
 */

import type {
  ForecastRunRead,
  ForecastRunSummaryRead,
  ProjectionYearRead,
  ResolvedForecastAssumptions,
} from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';

// ---------------------------------------------------------------------------
// Scenario vocabulary
// ---------------------------------------------------------------------------

export const SCENARIO_LABELS: Record<string, string> = {
  base: 'Base case',
  adverse: 'Adverse',
  severely_adverse: 'Severely adverse',
  custom: 'Custom',
};

export function scenarioLabel(code: string): string {
  return SCENARIO_LABELS[code] ?? code;
}

// ---------------------------------------------------------------------------
// Assumption registry — the 10 fields the forecast engine resolves per run.
// Keys mirror ResolvedForecastAssumptions (camel) and the API payload (snake).
// ---------------------------------------------------------------------------

export type AssumptionKey = keyof ResolvedForecastAssumptions;

export type AssumptionField = {
  key: AssumptionKey;
  apiKey: string;
  label: string;
  /** Plain-language definition of what the engine does with the value. */
  definition: string;
  unit: string;
  min: number;
  max: number;
  step: number;
  group: 'Growth' | 'Margin & income' | 'Cost & risk' | 'Capital & mix';
  /**
   * Where the value comes from when the preset omits it — the three
   * engine-default fields per ForecastAssumptionDefaultsRead.
   */
  hasEngineDefault: boolean;
};

export const ASSUMPTION_FIELDS: AssumptionField[] = [
  {
    key: 'loanGrowthPct',
    apiKey: 'loan_growth_pct',
    label: 'Loan growth',
    definition:
      'Annual gross loan book growth applied to each projection year.',
    unit: '%',
    min: -20,
    max: 40,
    step: 0.5,
    group: 'Growth',
    hasEngineDefault: false,
  },
  {
    key: 'depositGrowthPct',
    apiKey: 'deposit_growth_pct',
    label: 'Deposit growth',
    definition: 'Annual customer deposit growth; funding gaps fall to the borrowings plug.',
    unit: '%',
    min: -20,
    max: 40,
    step: 0.5,
    group: 'Growth',
    hasEngineDefault: false,
  },
  {
    key: 'nimPct',
    apiKey: 'nim_pct',
    label: 'Net interest margin',
    definition: 'Net interest income as a share of earning assets — drives the NII path.',
    unit: '%',
    min: 0,
    max: 12,
    step: 0.1,
    group: 'Margin & income',
    hasEngineDefault: false,
  },
  {
    key: 'feeIncomePctAssets',
    apiKey: 'fee_income_pct_assets',
    label: 'Fee income',
    definition: 'Fees and commissions as a share of total assets.',
    unit: '%',
    min: 0,
    max: 5,
    step: 0.1,
    group: 'Margin & income',
    hasEngineDefault: true,
  },
  {
    key: 'costToIncomePct',
    apiKey: 'cost_to_income_pct',
    label: 'Cost-to-income',
    definition: 'Operating expenses as a share of total income.',
    unit: '%',
    min: 20,
    max: 90,
    step: 0.5,
    group: 'Cost & risk',
    hasEngineDefault: false,
  },
  {
    key: 'creditLossRatePct',
    apiKey: 'credit_loss_rate_pct',
    label: 'Credit loss rate',
    definition: 'Annual provisions charged as a share of gross loans.',
    unit: '%',
    min: 0,
    max: 10,
    step: 0.1,
    group: 'Cost & risk',
    hasEngineDefault: false,
  },
  {
    key: 'fxDepreciationPct',
    apiKey: 'fx_depreciation_pct',
    label: 'FX depreciation',
    definition:
      'Annual cedi depreciation applied to FX-linked risk-weighted assets.',
    unit: '%',
    min: -10,
    max: 60,
    step: 1,
    group: 'Cost & risk',
    hasEngineDefault: false,
  },
  {
    key: 'taxRatePct',
    apiKey: 'tax_rate_pct',
    label: 'Tax rate',
    definition: 'Effective corporate tax rate applied to pre-tax profit.',
    unit: '%',
    min: 0,
    max: 50,
    step: 0.5,
    group: 'Cost & risk',
    hasEngineDefault: true,
  },
  {
    key: 'dividendPayoutPct',
    apiKey: 'dividend_payout_pct',
    label: 'Dividend payout',
    definition:
      'Share of net income distributed — the remainder retains into equity and CAR.',
    unit: '%',
    min: 0,
    max: 100,
    step: 1,
    group: 'Capital & mix',
    hasEngineDefault: false,
  },
  {
    key: 'securitiesShiftPp',
    apiKey: 'securities_shift_pp',
    label: 'Securities shift',
    definition:
      'Asset-mix shift from loans into securities, in percentage points.',
    unit: ' pp',
    min: -20,
    max: 20,
    step: 0.5,
    group: 'Capital & mix',
    hasEngineDefault: true,
  },
];

// ---------------------------------------------------------------------------
// Run helpers
// ---------------------------------------------------------------------------

/** Latest succeeded run id, optionally restricted to one scenario code. */
export function latestSucceededId(
  runs: ForecastRunSummaryRead[],
  scenarioCode?: string
): string | null {
  return (
    runs.find(
      (r) =>
        r.status === 'succeeded' &&
        (scenarioCode === undefined || r.scenarioCode === scenarioCode)
    )?.id ?? null
  );
}

export function yearLabel(point: ProjectionYearRead): string {
  return point.year === 0 ? `Y0 · ${point.periodLabel}` : `Y${point.year}`;
}

/** Threshold for a run metric (e.g. year5_car_pct), from the persisted metric results. */
export function metricThreshold(
  run: ForecastRunRead | undefined,
  metricCode: string,
  fallback: number
): number {
  const raw = run?.metricResults.find((m) => m.metricCode === metricCode)
    ?.thresholdMin;
  return raw === null || raw === undefined ? fallback : num(raw);
}

export function metricStatus(
  run: ForecastRunRead | undefined,
  metricCode: string
): string | null {
  return (
    run?.metricResults.find((m) => m.metricCode === metricCode)?.status ?? null
  );
}

/**
 * Liabilities per projection year, derived from the balance-sheet identity
 * liabilities = total assets − equity on the persisted path. (The path stores
 * deposits and the borrowings plug separately; the identity keeps the chart
 * consistent with the stored totals.)
 */
export function liabilitiesOf(p: ProjectionYearRead): number {
  return num(p.totalAssets) - num(p.equity);
}

/** Year-over-year % change; null for the first point or a zero base. */
export function yoyPct(current: number, previous: number | null): number | null {
  if (previous === null || previous === 0) return null;
  return ((current - previous) / Math.abs(previous)) * 100;
}
