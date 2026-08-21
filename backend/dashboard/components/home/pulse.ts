'use client';

/**
 * Shared pulse-card model for the Command Center: one headline card per
 * regulatory module, built from current module dashboards and the live
 * forecast baseline. Used by the pulse wall (rendering) and the
 * breach banner (status synthesis) — TanStack Query dedupes the underlying
 * dashboard fetches between them.
 *
 * Real data only: deltas and sparklines come from the dashboards' per-period
 * trend series and are omitted when no prior point exists; statuses prefer
 * the module's live block and fall back to the typed dashboard statuses.
 */

import type { LiveModule } from '@aequoros/risk-service-api';
import type { StatusTone } from '@/components/ui/StatusPill';
import { livePrimaryMetricKey } from '@/components/live/moduleDisplay';
import {
  useCapitalDashboard,
  useLiveSnapshots,
  useFtpDashboard,
  useFxDashboard,
  useIrrDashboard,
  useLiquidityDashboard,
  useLiveSummary,
} from '@/lib/api/hooks';
import { num } from '@/lib/api/values';
import { useModuleScope } from '@/components/shell/BankContext';
import { isHrefVisible } from '@/lib/modules';
import { LIVE_MODULE_HREFS } from '@/components/live/moduleDisplay';

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
  'rating',
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
  /** 'close' when delta/spark ride the daily EOD ladder, else monthly. */
  deltaBasis?: 'close' | 'period';
  computedAt?: Date | string | null;
  /** Basis note shown when there is no live computed-at timestamp. */
  basisNote?: string;
};

// --- trend helpers ---------------------------------------------------------

type TrendPoint = { reportingPeriodId: string };

/** Value change vs the previous trend point, or undefined when unavailable. */
function trendDelta<T extends TrendPoint>(
  trend: T[] | undefined,
  periodId: string | undefined,
  pick: (p: T) => number
): number | undefined {
  if (!trend || !periodId) return undefined;
  const idx = trend.findIndex((p) => p.reportingPeriodId === periodId);
  if (idx <= 0) return undefined;
  return pick(trend[idx]) - pick(trend[idx - 1]);
}

/** Prior-close delta + daily spark from the plane-2 EOD ladder. */
function ladderOverlay(
  snapshots: { metrics: { [key: string]: any } }[] | undefined,
  key: string
): { delta: number; spark: number[] } | null {
  if (!snapshots || snapshots.length < 2) return null;
  const values = snapshots
    .map((s) => Number(s.metrics?.[key]))
    .filter((v) => Number.isFinite(v));
  if (values.length < 2) return null;
  return {
    delta: values[values.length - 1] - values[values.length - 2],
    spark: values.slice(-31),
  };
}

/** Up to the last 12 trend values ending at the effective period. */
function trendSpark<T extends TrendPoint>(
  trend: T[] | undefined,
  periodId: string | undefined,
  pick: (p: T) => number
): number[] | undefined {
  if (!trend || !periodId) return undefined;
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
  hasData: boolean
): PulseCards {
  // Two gates on every module request:
  //  1. scope — an SDI raises no request for the FX/FTP engines it does not run
  //     (docs/sdi.md §3.2); the wall filters those cards too.
  //  2. hasData — a tenant with no current facts yet does NOT fetch the module
  //     dashboards/ladders at all: they would 409 ("current_facts_missing") and
  //     redden the console for an expected pre-ingestion state. The Command
  //     Center's "No computed data yet" panel explains it instead.
  const scope = useModuleScope();
  const scoped = (module: LiveModule) => isHrefVisible(LIVE_MODULE_HREFS[module], scope);
  const dataBankId = hasData ? bankId : undefined;
  const liq = useLiquidityDashboard(dataBankId);
  const cap = useCapitalDashboard(dataBankId);
  const irr = useIrrDashboard(scoped('irr') ? dataBankId : undefined);
  const fx = useFxDashboard(scoped('fx') ? dataBankId : undefined);
  const ftp = useFtpDashboard(scoped('ftp') ? dataBankId : undefined);
  const liveSummary = useLiveSummary(bankId);
  const ratingLive = liveSummary.data?.modules.find((module) => module.module === 'rating');
  const forecastLive = liveSummary.data?.modules.find((module) => module.module === 'forecast');

  // Plane-2 EOD ladders — when at least two daily points exist, the card's
  // delta and sparkline switch from month-over-month to prior-close. Gated by
  // scope AND data availability like the dashboards above.
  const ladderId = (module: LiveModule) =>
    hasData && scoped(module) ? bankId : undefined;
  const ladders = {
    liquidity: useLiveSnapshots(ladderId('liquidity'), 'liquidity'),
    capital: useLiveSnapshots(ladderId('capital'), 'capital'),
    irr: useLiveSnapshots(ladderId('irr'), 'irr'),
    fx: useLiveSnapshots(ladderId('fx'), 'fx'),
    ftp: useLiveSnapshots(ladderId('ftp'), 'ftp'),
    rating: useLiveSnapshots(ladderId('rating'), 'rating'),
    forecast: useLiveSnapshots(ladderId('forecast'), 'forecast'),
  } as const;

  const baseCards: Record<LiveModule, PulseCardModel> = {
    liquidity: {
      module: 'liquidity',
      isLoading: liq.isLoading,
      error: liq.error,
      ...(liq.data && {
        metricLabel: 'Liquidity Coverage Ratio',
        value: fixed(num(liq.data.metrics.lcrPct), 2),
        unit: '%',
        delta: trendDelta(liq.data.trend, liq.data.period.id, (p) => num(p.lcrPct)),
        spark: trendSpark(liq.data.trend, liq.data.period.id, (p) => num(p.lcrPct)),
        hint: `NSFR ${fixed(num(liq.data.metrics.nsfrPct), 2)}%`,
        computedAt: liq.data.live?.computedAt ?? null,
        basisNote: 'current live calculation',
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
        delta: trendDelta(cap.data.trend, cap.data.period.id, (p) => num(p.carPct)),
        spark: trendSpark(cap.data.trend, cap.data.period.id, (p) => num(p.carPct)),
        hint: `Tier 1 ${fixed(num(cap.data.metrics.tier1RatioPct), 2)}% · CET1 ${fixed(
          num(cap.data.metrics.cet1RatioPct),
          2
        )}%`,
        computedAt: cap.data.live?.computedAt ?? null,
        basisNote: 'current live calculation',
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
        delta: trendDelta(irr.data.trend, irr.data.period.id, (p) =>
          num(p.worstEveChangePctTier1)
        ),
        invertDelta: true,
        spark: trendSpark(irr.data.trend, irr.data.period.id, (p) =>
          num(p.worstEveChangePctTier1)
        ),
        hint: `Duration gap ${fixed(num(irr.data.metrics.durationGap), 2)}y · limit ${fixed(
          num(irr.data.metrics.eveLimitPct),
          0
        )}%`,
        computedAt: irr.data.live?.computedAt ?? null,
        basisNote: 'current live calculation',
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
        delta: trendDelta(fx.data.trend, fx.data.period.id, (p) => num(p.nopPctTier1)),
        invertDelta: true,
        spark: trendSpark(fx.data.trend, fx.data.period.id, (p) => num(p.nopPctTier1)),
        hint: `Largest single ccy ${fx.data.metrics.singleCcyMaxCurrency} ${fixed(
          num(fx.data.metrics.singleCcyMaxPct),
          2
        )}%`,
        computedAt: fx.data.live?.computedAt ?? null,
        basisNote: 'current live calculation',
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
        delta: trendDelta(ftp.data.trend, ftp.data.period.id, (p) =>
          num(p.portfolioNimPct)
        ),
        spark: trendSpark(ftp.data.trend, ftp.data.period.id, (p) =>
          num(p.portfolioNimPct)
        ),
        hint: `${ftp.data.metrics.productsBelowMinMargin} of ${ftp.data.metrics.totalProducts} products below margin floor`,
        computedAt: ftp.data.live?.computedAt ?? null,
        basisNote: 'current live calculation',
      }),
      status: ftp.data
        ? (ftp.data.live?.status ?? ftp.data.metrics.nmdCoreStatus)
        : 'na',
    },
    rating: {
      module: 'rating',
      isLoading: liveSummary.isLoading,
      error: liveSummary.error,
      ...(ratingLive?.metrics.availability !== 'unavailable' && ratingLive && {
        metricLabel: 'Conservative PIT PD band',
        value: fixed(num(ratingLive.metrics.pit_pd_upper_pct), 2),
        unit: '%',
        hint: `Implied ${String(ratingLive.metrics.pit_rating_grade ?? '—').toUpperCase()} · sovereign ceiling ${String(ratingLive.metrics.sovereign_ceiling ?? '—').toUpperCase()}`,
        computedAt: ratingLive.computedAt,
        basisNote: 'live canonical scorecard',
      }),
      status: (ratingLive?.status ?? 'na') as CardStatus,
    },
    forecast: buildForecastCard(forecastLive, liveSummary.isLoading, liveSummary.error),
  };

  const cards = Object.fromEntries(
    DEFAULT_MODULE_ORDER.map((module) => {
      const card = baseCards[module];
      const overlay = ladderOverlay(
        ladders[module].data?.snapshots,
        livePrimaryMetricKey(module)
      );
      return [
        module,
        overlay
          ? { ...card, delta: overlay.delta, spark: overlay.spark, deltaBasis: 'close' as const }
          : { ...card, deltaBasis: 'period' as const },
      ];
    })
  ) as Record<LiveModule, PulseCardModel>;

  return {
    cards,
    isLoading: DEFAULT_MODULE_ORDER.some((m) => cards[m].isLoading),
  };
}

function buildForecastCard(
  forecast: { metrics: Record<string, unknown>; status: CardStatus; computedAt: Date } | undefined,
  isLoading: boolean,
  error: unknown
): PulseCardModel {
  const base: PulseCardModel = {
    module: 'forecast',
    isLoading,
    error,
    status: 'na',
  };
  if (!forecast || forecast.status === 'na') {
    return {
      ...base,
      pill: { tone: 'slate', label: 'Unavailable' },
      hint: 'Current forecast baseline is not available yet',
    };
  }
  const metric = (key: string) => forecast.metrics[key] as string | number | undefined;
  return {
    ...base,
    metricLabel: 'Year-5 CAR (projected)',
    value: fixed(num(metric('year5_car_pct')), 2),
    unit: '%',
    status: forecast.status,
    hint: `Current base assumptions · minimum LCR ${fixed(num(metric('min_lcr_pct')), 2)}%`,
    computedAt: forecast.computedAt,
    basisNote: 'current live forecast baseline',
  };
}
