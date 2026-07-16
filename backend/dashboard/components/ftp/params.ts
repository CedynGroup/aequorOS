/**
 * Typed, guarded readers over the FTP regulatory run's input snapshot
 * (`run.inputs.parameters`). The backend persists the exact threshold set
 * and stress overlays each run priced with; these helpers surface that
 * snapshot for the Rules page without inventing any numbers.
 */

import type { RegulatoryRunRead } from '@aequoros/risk-service-api';

export type FtpRunParameters = {
  targetRoePct: number | null;
  minProductMarginPct: number | null;
  liquidityPremiumMaxBps: number | null;
  fundingSpreadMaxBps: number | null;
  nmdCoreMinPct: number | null;
  nmdCoreMaxPct: number | null;
  /** rates_up_200 parallel curve shift, in bp. */
  ratesUpShiftBps: number | null;
  /** funding_stress additive funding spread, in bp. */
  fundingStressAddBps: number | null;
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

/** Parse the parameter snapshot out of a stored FTP run, if present. */
export function ftpRunParameters(
  run: RegulatoryRunRead | undefined
): FtpRunParameters | null {
  const params = asRecord(asRecord(run?.inputs)?.['parameters']);
  if (!params) return null;

  const thresholds = asRecord(params['thresholds']) ?? {};
  const overlays = asRecord(params['stress_overlays_bps']) ?? {};

  return {
    targetRoePct: finite(thresholds['ftp_target_roe_pct']),
    minProductMarginPct: finite(thresholds['ftp_min_product_margin_pct']),
    liquidityPremiumMaxBps: finite(thresholds['ftp_liquidity_premium_max_bps']),
    fundingSpreadMaxBps: finite(thresholds['ftp_funding_spread_max_bps']),
    nmdCoreMinPct: finite(thresholds['nmd_core_min_pct']),
    nmdCoreMaxPct: finite(thresholds['nmd_core_max_pct']),
    ratesUpShiftBps: finite(overlays['rates_up_200']),
    fundingStressAddBps: finite(overlays['funding_stress']),
  };
}
