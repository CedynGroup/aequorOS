/**
 * Defensive readers for the per-currency ladder blobs a stored liquidity run
 * carries in its untyped `metrics` dict (written by
 * backend/app/services/regulatory_liquidity.py):
 *
 *   metrics.currency_gaps: [{ currency, assets[], liabilities[], net[],
 *     cumulative[], assets_total, liabilities_total, net_total,
 *     stressed_liabilities_total, stressed_net_total }]
 *   metrics.stressed_ladder: [{ currency, contractual_liabilities[],
 *     stressed_liabilities[], stressed_net[], stressed_cumulative[],
 *     demand_deposits, stable_core }]   — only when the run's scenario
 *     carried a reviewed NMD run-off schedule (LRMD ¶50–54).
 *
 * Every amount is a stringified Decimal; every per-horizon array has exactly
 * five entries (30 / 91 / 182 / 365 / beyond — `_LADDER_HORIZON_DAYS`).
 * Anything that fails those shape checks is dropped rather than guessed at,
 * so the panel renders an honest empty state instead of misaligned bars.
 */

import type { RegulatoryRunRead } from '@aequoros/risk-service-api';

export const LADDER_HORIZONS = [
  { key: 'h30', label: '≤ 30 days' },
  { key: 'h91', label: '31–91 days' },
  { key: 'h182', label: '92–182 days' },
  { key: 'h365', label: '183–365 days' },
  { key: 'beyond', label: '> 365 days' },
] as const;

export type CurrencyGapEntry = {
  currency: string;
  assets: number[];
  liabilities: number[];
  net: number[];
  cumulative: number[];
};

export type StressedLadderEntry = {
  currency: string;
  contractualLiabilities: number[];
  stressedLiabilities: number[];
  stressedNet: number[];
  stressedCumulative: number[];
  demandDeposits: number;
  stableCore: number;
};

function asFiniteNumber(value: unknown): number | null {
  if (typeof value !== 'string' && typeof value !== 'number') return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A per-horizon series: exactly one finite number per ladder horizon. */
function asHorizonSeries(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length !== LADDER_HORIZONS.length) {
    return null;
  }
  const series: number[] = [];
  for (const raw of value) {
    const parsed = asFiniteNumber(raw);
    if (parsed === null) return null;
    series.push(parsed);
  }
  return series;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function parseCurrencyGaps(
  run: RegulatoryRunRead | undefined
): CurrencyGapEntry[] {
  const raw: unknown = run?.metrics?.['currency_gaps'];
  if (!Array.isArray(raw)) return [];
  const entries: CurrencyGapEntry[] = [];
  for (const item of raw) {
    if (!isRecord(item) || typeof item['currency'] !== 'string') continue;
    const assets = asHorizonSeries(item['assets']);
    const liabilities = asHorizonSeries(item['liabilities']);
    const net = asHorizonSeries(item['net']);
    const cumulative = asHorizonSeries(item['cumulative']);
    if (!assets || !liabilities || !net || !cumulative) continue;
    entries.push({ currency: item['currency'], assets, liabilities, net, cumulative });
  }
  return entries;
}

export function parseStressedLadder(
  run: RegulatoryRunRead | undefined
): StressedLadderEntry[] {
  const raw: unknown = run?.metrics?.['stressed_ladder'];
  if (!Array.isArray(raw)) return [];
  const entries: StressedLadderEntry[] = [];
  for (const item of raw) {
    if (!isRecord(item) || typeof item['currency'] !== 'string') continue;
    const contractualLiabilities = asHorizonSeries(item['contractual_liabilities']);
    const stressedLiabilities = asHorizonSeries(item['stressed_liabilities']);
    const stressedNet = asHorizonSeries(item['stressed_net']);
    const stressedCumulative = asHorizonSeries(item['stressed_cumulative']);
    const demandDeposits = asFiniteNumber(item['demand_deposits']);
    const stableCore = asFiniteNumber(item['stable_core']);
    if (
      !contractualLiabilities ||
      !stressedLiabilities ||
      !stressedNet ||
      !stressedCumulative ||
      demandDeposits === null ||
      stableCore === null
    ) {
      continue;
    }
    entries.push({
      currency: item['currency'],
      contractualLiabilities,
      stressedLiabilities,
      stressedNet,
      stressedCumulative,
      demandDeposits,
      stableCore,
    });
  }
  return entries;
}
