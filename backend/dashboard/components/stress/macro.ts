/**
 * Macro-variable registry for the stress scenario builder (docs/stress.md §3.1,
 * AppIII Table 6). Mirrors the backend's ``MACRO_VARIABLES`` (app/models/stress.py)
 * — the 13 Table 6 drivers a governed scenario authors as base/stress paths over
 * a ≥3-year horizon. Each entry carries the directive unit and how the value is
 * displayed (a fraction rendered as a percentage vs a raw level).
 *
 * These are the ONLY variables the backend accepts; an unknown key is rejected
 * server-side (schemas/stress.py::MacroPathIn), so the builder is closed to this
 * set by construction.
 */

export type MacroVarKind = 'fraction' | 'level';

export type MacroVarMeta = {
  key: string;
  label: string;
  /** Short unit caption shown under the grid header. */
  unit: string;
  /** `fraction` values are stored as decimals and shown ×100 as a percentage. */
  kind: MacroVarKind;
  /** Directive grouping for the palette. */
  group: 'activity' | 'rates' | 'fx' | 'markets' | 'commodities';
};

export const MACRO_VARS: MacroVarMeta[] = [
  { key: 'gdp_growth', label: 'Real GDP growth', unit: '% YoY', kind: 'fraction', group: 'activity' },
  { key: 'inflation', label: 'Headline inflation (CPI)', unit: '% YoY', kind: 'fraction', group: 'activity' },
  { key: 'unemployment', label: 'Unemployment rate', unit: '%', kind: 'fraction', group: 'activity' },
  { key: 'fiscal_deficit', label: 'Fiscal deficit / GDP', unit: '% of GDP', kind: 'fraction', group: 'activity' },
  { key: 'policy_rate', label: 'Monetary Policy Rate', unit: '%', kind: 'fraction', group: 'rates' },
  { key: 'interest_rate', label: 'Market / lending rate', unit: '%', kind: 'fraction', group: 'rates' },
  { key: 'gog_yield', label: 'GoG securities yield', unit: '%', kind: 'fraction', group: 'rates' },
  { key: 'fx_usd_ghs', label: 'USD → GHS', unit: 'GHS per USD', kind: 'level', group: 'fx' },
  { key: 'fx_gbp_ghs', label: 'GBP → GHS', unit: 'GHS per GBP', kind: 'level', group: 'fx' },
  { key: 'fx_eur_ghs', label: 'EUR → GHS', unit: 'GHS per EUR', kind: 'level', group: 'fx' },
  { key: 'gse_index', label: 'GSE Composite Index', unit: 'index level', kind: 'level', group: 'markets' },
  { key: 'cocoa_price', label: 'Cocoa price', unit: 'USD / tonne', kind: 'level', group: 'commodities' },
  { key: 'gold_price', label: 'Gold price', unit: 'USD / oz', kind: 'level', group: 'commodities' },
];

export const MACRO_VAR_BY_KEY: Record<string, MacroVarMeta> = Object.fromEntries(
  MACRO_VARS.map((v) => [v.key, v])
);

export const MACRO_GROUPS: { key: MacroVarMeta['group']; label: string }[] = [
  { key: 'activity', label: 'Real economy' },
  { key: 'rates', label: 'Rates' },
  { key: 'fx', label: 'Foreign exchange' },
  { key: 'markets', label: 'Markets' },
  { key: 'commodities', label: 'Commodities' },
];

/** Format a stored macro value for display given its kind. */
export function fmtMacro(meta: MacroVarMeta | undefined, value: number): string {
  if (!Number.isFinite(value)) return '—';
  if (meta?.kind === 'fraction') return `${(value * 100).toFixed(2)}%`;
  return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
}

export const SCENARIO_TYPES = [
  'base',
  'adverse',
  'historical',
  'hypothetical',
  'reverse',
  'supervisory',
] as const;
export type ScenarioType = (typeof SCENARIO_TYPES)[number];

export const SEVERITIES = ['mild', 'moderate', 'severe'] as const;
export type Severity = (typeof SEVERITIES)[number];

export const SCENARIO_STATUSES = [
  'draft',
  'pending_approval',
  'approved',
  'archived',
] as const;
export type ScenarioStatus = (typeof SCENARIO_STATUSES)[number];
