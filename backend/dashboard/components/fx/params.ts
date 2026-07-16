/**
 * Typed, guarded readers over the FX regulatory run's input snapshot
 * (`run.inputs`). The backend persists the exact parameter set and fact list
 * each run was computed from; these helpers surface that snapshot for
 * display (crisis window, hedge bands, per-currency position split) without
 * ever recomputing anything client-side.
 */

import type { RegulatoryRunRead } from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';

export type FxCrisisParams = {
  /** Inclusive observation-index window of the cedi-crisis return slice. */
  windowStart: number;
  windowEnd: number;
  /** Supervisory correlation uplift as a decimal fraction (e.g. 0.25). */
  correlationUplift: number;
};

export type FxHedgeBands = {
  r2MinPct: number;
  offsetLowPct: number;
  offsetHighPct: number;
};

export type FxRunParameters = {
  crisis: FxCrisisParams | null;
  hedgeBands: FxHedgeBands | null;
  /** scenario_code -> depreciation shock % */
  depreciationShocksPct: Record<string, number>;
};

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function finite(value: unknown): number | null {
  const parsed =
    typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

/** Parse the parameter snapshot out of a stored FX run, if present. */
export function fxRunParameters(
  run: RegulatoryRunRead | undefined
): FxRunParameters | null {
  const params = asRecord(asRecord(run?.inputs)?.['parameters']);
  if (!params) return null;

  const crisisRaw = asRecord(params['crisis']);
  const windowStart = finite(crisisRaw?.['window_start']);
  const windowEnd = finite(crisisRaw?.['window_end']);
  const uplift = finite(crisisRaw?.['correlation_uplift']);
  const crisis: FxCrisisParams | null =
    windowStart !== null && windowEnd !== null && uplift !== null
      ? { windowStart, windowEnd, correlationUplift: uplift }
      : null;

  const bandsRaw = asRecord(params['hedge_bands_pct']);
  const r2Min = finite(bandsRaw?.['hedge_r2_min_pct']);
  const offsetLow = finite(bandsRaw?.['hedge_offset_low_pct']);
  const offsetHigh = finite(bandsRaw?.['hedge_offset_high_pct']);
  const hedgeBands: FxHedgeBands | null =
    r2Min !== null && offsetLow !== null && offsetHigh !== null
      ? { r2MinPct: r2Min, offsetLowPct: offsetLow, offsetHighPct: offsetHigh }
      : null;

  const shocksRaw = asRecord(params['depreciation_shocks_pct']) ?? {};
  const depreciationShocksPct: Record<string, number> = {};
  for (const [code, value] of Object.entries(shocksRaw)) {
    const parsed = finite(value);
    if (parsed !== null) depreciationShocksPct[code] = parsed;
  }

  return { crisis, hedgeBands, depreciationShocksPct };
}

export type FxPositionSplit = {
  currency: string;
  assetsCcy: number;
  liabilitiesCcy: number;
  netDerivativesCcy: number;
  netCcy: number;
};

/**
 * Per-currency asset / liability / derivative split from the stored run's
 * fact snapshot (`fact_group === 'fx_position'`). Empty when no run is
 * stored for the period.
 */
export function fxPositionSplits(
  run: RegulatoryRunRead | undefined
): Map<string, FxPositionSplit> {
  const splits = new Map<string, FxPositionSplit>();
  const facts = asRecord(run?.inputs)?.['facts'];
  if (!Array.isArray(facts)) return splits;

  for (const entry of facts) {
    const fact = asRecord(entry);
    if (!fact || fact['fact_group'] !== 'fx_position') continue;
    const attributes = asRecord(fact['attributes']);
    const currency = attributes?.['currency'];
    if (!attributes || typeof currency !== 'string') continue;
    splits.set(currency, {
      currency,
      assetsCcy: num(finite(attributes['assets_ccy']) ?? 0),
      liabilitiesCcy: num(finite(attributes['liabilities_ccy']) ?? 0),
      netDerivativesCcy: num(finite(attributes['net_derivatives_ccy']) ?? 0),
      netCcy: num(finite(attributes['net_ccy']) ?? 0),
    });
  }
  return splits;
}
