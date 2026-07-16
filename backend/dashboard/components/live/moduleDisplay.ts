/**
 * Shared display metadata for the live engine's six modules: human labels,
 * dashboard routes, and the one headline metric surfaced in the live status
 * card. Live/freshness payloads carry raw snake_case metric keys, so the
 * headline lookup reads those keys directly.
 */

import type { LiveModule } from '@aequoros/risk-service-api';
import { num } from '@/lib/api/values';
import { fmtPct } from '@/lib/format';

export const LIVE_MODULE_LABELS: Record<LiveModule, string> = {
  liquidity: 'Liquidity',
  capital: 'Capital',
  irr: 'Interest Rate Risk',
  fx: 'FX Risk',
  ftp: 'Transfer Pricing',
  forecast: 'Balance Sheet Forecast',
};

export const LIVE_MODULE_HREFS: Record<LiveModule, string> = {
  liquidity: '/liquidity',
  capital: '/basel',
  irr: '/irr',
  fx: '/fx',
  ftp: '/ftp',
  forecast: '/forecasting',
};

export type LivePrimaryMetric = { label: string; value: string };

// The single most representative metric per module, by its raw payload key.
const PRIMARY_METRIC: Record<LiveModule, { key: string; label: string }> = {
  liquidity: { key: 'lcr_pct', label: 'LCR' },
  capital: { key: 'car_pct', label: 'CAR' },
  irr: { key: 'eve_limit_pct', label: 'ΔEVE / Tier 1' },
  fx: { key: 'nop_pct_tier1', label: 'NOP / Tier 1' },
  ftp: { key: 'portfolio_nim_pct', label: 'Portfolio NIM' },
  forecast: { key: 'year5_car_pct', label: 'Year-5 CAR' },
};

/** Headline metric for a module's live block, or null when unavailable. */
export function livePrimaryMetric(
  module: LiveModule,
  metrics: Record<string, unknown> | null | undefined
): LivePrimaryMetric | null {
  const spec = PRIMARY_METRIC[module];
  if (!spec || !metrics) return null;
  const raw = metrics[spec.key];
  if (raw === null || raw === undefined) return null;
  if (typeof raw !== 'string' && typeof raw !== 'number') return null;
  return { label: spec.label, value: fmtPct(num(raw), 2) };
}
