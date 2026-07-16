'use client';

/**
 * Shared pulse-card model for the Command Center: one headline card per
 * regulatory module, built entirely from the module dashboards and forecast
 * runs for the effective period. Used by the pulse wall (rendering) and the
 * breach banner (status synthesis) — TanStack Query dedupes the underlying
 * dashboard fetches between them.
 *
 * Real data only: deltas and sparklines come from the dashboards' per-period
 * trend series and are omitted when no prior point exists; statuses prefer
 * the module's live block and fall back to the typed dashboard statuses.
 */

import type {
  BankReportingPeriodRead,
  LiveModule,
} from '@aequoros/risk-service-api';
import type { StatusTone } from '@/components/ui/StatusPill';
import {
  useCapitalDashboard,
  useForecastRuns,
  useFtpDashboard,
  useFxDashboard,
  useIrrDashboard,
  useLiquidityDashboard,
} from '@/lib/api/hooks';
import { labelize, num } from '@/lib/api/values';

export type Traffic = 'green' | 'amber' | 'red';
export type CardStatus = Traffic | 'na';

export const STATUS_RANK: Record<CardStatus, number> = {
  red: 0,
  amber: 1,
  green: 2,
  na: 3,
};

export function worstOf(...statuses: Traffic[]): Traffic {
  return statuses.reduce((worst, s) =>
    STATUS_RANK[s] < STATUS_RANK[worst] ? s : worst
  );
}

/** toFixed that never renders negative zero ("-0.00" → "0.00"). */
export function fixed(value: number, decimals: number): string {
  const rendered = value.toFixed(decimals);
  return Number(rendered) === 0 ? (0).toFixed(decimals) : rendered;
}

export const DEFAULT_MODULE_ORDER: LiveModule[] = [
  'liquidity',
  'capital',
  'irr',
  'fx',
  'ftp',
  'forecast',
];

export type PulseCardModel = {
  module: LiveModule;
  isLoading: boolean;
  error?: unknown;
  metricLabel?: string;
  value?: string;
  unit?: string;
  status: CardStatus;
  /** Overrides the traffic-light pill (used by the forecast run card). */
  pill?: { tone: StatusTone; label: string };
  delta?: number;
  invertDelta?: boolean;
  hint?: string;
  spark?: number[];
  computedAt?: Date | string | null;
  /** Basis note shown when there is no live computed-at timestamp. */
  basisNote?: string;
};

// --- trend helpers ---------------------------------------------------------

type TrendPoint = { reportingPeriodId: string };

/** Value change vs the previous trend point, or undefined when unavailable. */
function trendDelta<T extends TrendPoint>(
  trend: T[] | undefined,
  periodId: string,
  pick: (p: T) => number
): number | undefined {
  if (!trend) return undefined;
  const idx = trend.findIndex((p) => p.reportingPeriodId === periodId);
  if (idx <= 0) return undefined;
  return pick(trend[idx]) - pick(trend[idx - 1]);
}

/** Up to the last 12 trend values ending at the effective period. */
function trendSpark<T extends TrendPoint>(
  trend: T[] | undefined,
  periodId: string,
  pick: (p: T) => number
): number[] | undefined {
  if (!trend) return undefined;
  const idx = trend.findIndex((p) => p.reportingPeriodId === periodId);
  if (idx < 1) return undefined;
  return trend.slice(Math.max(0, idx - 11), idx + 1).map(pick);
}

// --- hook --------------------------------------------------------------------

export type PulseCards = {
  cards: Record<LiveModule, PulseCardModel>;
  /** True while any module card is still fetching. */
  isLoading: boolean;
};

export function usePulseCards(
  bankId: string | undefined,
  period: BankReportingPeriodRead
): PulseCards {
  const periodId = period.id;

  const liq = useLiquidityDashboard(bankId, periodId);
  const cap = useCapitalDashboard(bankId, periodId);
  const irr = useIrrDashboard(bankId, periodId);
  const fx = useFxDashboard(bankId, periodId);
  const ftp = useFtpDashboard(bankId, periodId);
  const forecasts = useForecastRuns(bankId, { limit: 10 });

  const cards: Record<LiveModule, PulseCardModel> = {
    liquidity: {
      module: 'liquidity',
      isLoading: liq.isLoading,
      error: liq.error,
      ...(liq.data && {
        metricLabel: 'Liquidity Coverage Ratio',
        value: fixed(num(liq.data.metrics.lcrPct), 2),
        unit: '%',
        delta: trendDelta(liq.data.trend, periodId, (p) => num(p.lcrPct)),
        spark: trendSpark(liq.data.trend, periodId, (p) => num(p.lcrPct)),
        hint: `NSFR ${fixed(num(liq.data.metrics.nsfrPct), 2)}%`,
        computedAt: liq.data.live?.computedAt ?? null,
        basisNote: liq.data.stored ? 'stored baseline run' : 'computed inline',
      }),
      status: liq.data
        ? (liq.data.live?.status ??
          worstOf(liq.data.metrics.lcrStatus, liq.data.metrics.nsfrStatus))
        : 'na',
    },
    capital: {
      module: 'capital',
      isLoading: cap.isLoading,
      error: cap.error,
      ...(cap.data && {
        metricLabel: 'Capital Adequacy Ratio',
        value: fixed(num(cap.data.metrics.carPct), 2),
        unit: '%',
        delta: trendDelta(cap.data.trend, periodId, (p) => num(p.carPct)),
        spark: trendSpark(cap.data.trend, periodId, (p) => num(p.carPct)),
        hint: `Tier 1 ${fixed(num(cap.data.metrics.tier1RatioPct), 2)}% · CET1 ${fixed(
          num(cap.data.metrics.cet1RatioPct),
          2
        )}%`,
        computedAt: cap.data.live?.computedAt ?? null,
        basisNote: cap.data.stored ? 'stored baseline run' : 'computed inline',
      }),
      status: cap.data
        ? (cap.data.live?.status ??
          worstOf(
            cap.data.metrics.carStatus,
            cap.data.metrics.tier1Status,
            cap.data.metrics.cet1Status,
            cap.data.metrics.leverageStatus
          ))
        : 'na',
    },
    irr: {
      module: 'irr',
      isLoading: irr.isLoading,
      error: irr.error,
      ...(irr.data && {
        metricLabel: 'Worst ΔEVE / Tier 1',
        value: fixed(num(irr.data.metrics.worstEveChangePctTier1), 2),
        unit: '%',
        delta: trendDelta(irr.data.trend, periodId, (p) =>
          num(p.worstEveChangePctTier1)
        ),
        invertDelta: true,
        spark: trendSpark(irr.data.trend, periodId, (p) =>
          num(p.worstEveChangePctTier1)
        ),
        hint: `Duration gap ${fixed(num(irr.data.metrics.durationGap), 2)}y · limit ${fixed(
          num(irr.data.metrics.eveLimitPct),
          0
        )}%`,
        computedAt: irr.data.live?.computedAt ?? null,
        basisNote: irr.data.stored ? 'stored baseline run' : 'computed inline',
      }),
      status: irr.data
        ? (irr.data.live?.status ?? irr.data.metrics.eveStatus)
        : 'na',
    },
    fx: {
      module: 'fx',
      isLoading: fx.isLoading,
      error: fx.error,
      ...(fx.data && {
        metricLabel: 'Net Open Position / Tier 1',
        value: fixed(num(fx.data.metrics.nopPctTier1), 2),
        unit: '%',
        delta: trendDelta(fx.data.trend, periodId, (p) => num(p.nopPctTier1)),
        invertDelta: true,
        spark: trendSpark(fx.data.trend, periodId, (p) => num(p.nopPctTier1)),
        hint: `Largest single ccy ${fx.data.metrics.singleCcyMaxCurrency} ${fixed(
          num(fx.data.metrics.singleCcyMaxPct),
          2
        )}%`,
        computedAt: fx.data.live?.computedAt ?? null,
        basisNote: fx.data.stored ? 'stored baseline run' : 'computed inline',
      }),
      status: fx.data
        ? (fx.data.live?.status ??
          worstOf(fx.data.metrics.nopStatus, fx.data.metrics.singleCcyStatus))
        : 'na',
    },
    ftp: {
      module: 'ftp',
      isLoading: ftp.isLoading,
      error: ftp.error,
      ...(ftp.data && {
        metricLabel: 'Portfolio NIM (weighted)',
        value: fixed(num(ftp.data.metrics.portfolioNimPct), 2),
        unit: '%',
        delta: trendDelta(ftp.data.trend, periodId, (p) =>
          num(p.portfolioNimPct)
        ),
        spark: trendSpark(ftp.data.trend, periodId, (p) =>
          num(p.portfolioNimPct)
        ),
        hint: `${ftp.data.metrics.productsBelowMinMargin} of ${ftp.data.metrics.totalProducts} products below margin floor`,
        computedAt: ftp.data.live?.computedAt ?? null,
        basisNote: ftp.data.stored ? 'stored baseline run' : 'computed inline',
      }),
      status: ftp.data
        ? (ftp.data.live?.status ?? ftp.data.metrics.nmdCoreStatus)
        : 'na',
    },
    forecast: buildForecastCard(forecasts, periodId),
  };

  return {
    cards,
    isLoading: DEFAULT_MODULE_ORDER.some((m) => cards[m].isLoading),
  };
}

function buildForecastCard(
  forecasts: ReturnType<typeof useForecastRuns>,
  periodId: string
): PulseCardModel {
  const base: PulseCardModel = {
    module: 'forecast',
    isLoading: forecasts.isLoading,
    error: forecasts.error,
    status: 'na',
  };
  const runs = forecasts.data?.runs ?? [];
  if (runs.length === 0) {
    return {
      ...base,
      pill: { tone: 'slate', label: 'No runs' },
      hint: 'Run a 5-year projection from the Forecasting module',
    };
  }
  // Newest run for the effective period, else the newest run overall.
  const latest = runs.find((r) => r.reportingPeriodId === periodId) ?? runs[0];
  const pill: PulseCardModel['pill'] =
    latest.status === 'succeeded'
      ? { tone: 'success', label: 'Succeeded' }
      : latest.status === 'failed'
        ? { tone: 'critical', label: 'Failed' }
        : { tone: 'pending', label: labelize(latest.status) };
  return {
    ...base,
    metricLabel: 'Year-5 CAR (projected)',
    value:
      latest.year5CarPct !== null && latest.year5CarPct !== undefined
        ? fixed(num(latest.year5CarPct), 2)
        : undefined,
    unit:
      latest.year5CarPct !== null && latest.year5CarPct !== undefined
        ? '%'
        : undefined,
    pill,
    hint: `${labelize(latest.scenarioCode)} scenario · period ${latest.periodLabel}`,
    computedAt: latest.createdAt,
  };
}
