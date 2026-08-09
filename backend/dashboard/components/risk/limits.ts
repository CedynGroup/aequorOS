/**
 * Cross-module limit extraction for the Risk & Limit Monitor.
 *
 * Normalizes every limit-type metric the module dashboards expose into one
 * shape. HARD RULE: a row is only emitted when its numeric threshold exists
 * in the API payload — this module never invents or hardcodes a regulatory
 * threshold. Metrics whose payloads carry a status but no numeric threshold
 * (e.g. liquidity LCR/NSFR) are surfaced through the validation checks tab
 * instead of the limit wall.
 */

import type {
  CapitalDashboardRead,
  RegulatoryRunRead,
  FtpDashboardRead,
  FxDashboardRead,
  IrrDashboardRead,
  LiquidityDashboardRead,
} from '@aequoros/risk-service-api';
import { labelize, num } from '@/lib/api/values';
import { runThresholds } from '@/components/liquidity/runData';
import { fmtCurrencySigned } from '@/lib/format';

export type LimitModule = 'liquidity' | 'capital' | 'irr' | 'fx' | 'ftp';
export type LimitStatus = 'ok' | 'warn' | 'crit';
export type LimitDirection = 'above' | 'below';

export type LimitRow = {
  module: LimitModule;
  /** Human label of the limit. */
  limit: string;
  value: number;
  /** Numeric threshold taken verbatim from the payload — never invented. */
  threshold: number;
  /** Amber threshold, only when the payload carries one. */
  warnAt?: number;
  direction: LimitDirection;
  status: LimitStatus;
  unit: string;
  computedAt?: Date;
  /** Extra payload context (scenario, currency side, counts). */
  detail?: string;
};

export type ModuleValidation = {
  ruleCode: string;
  passed: boolean;
  severity: string;
  message: string;
};

export const MODULE_LABELS: Record<LimitModule, string> = {
  liquidity: 'Liquidity',
  capital: 'Capital',
  irr: 'Interest Rate Risk',
  fx: 'FX Risk',
  ftp: 'FTP',
};

export const MODULE_HREFS: Record<LimitModule, string> = {
  liquidity: '/liquidity',
  capital: '/basel',
  irr: '/irr',
  fx: '/fx',
  ftp: '/ftp',
};

/** Backend traffic light → wall status. */
function fromTrafficLight(status: string): LimitStatus {
  if (status === 'red') return 'crit';
  if (status === 'amber') return 'warn';
  return 'ok';
}

export function capitalLimits(data: CapitalDashboardRead | undefined): LimitRow[] {
  if (!data) return [];
  const buffers = data.buffers;
  return [
    {
      module: 'capital',
      limit: 'Capital adequacy ratio (CAR)',
      value: num(data.metrics.carPct),
      threshold: num(buffers.carMinPct),
      warnAt: num(buffers.carEarlyWarningPct),
      direction: 'above',
      status: fromTrafficLight(data.metrics.carStatus),
      unit: '%',
      computedAt: data.live?.computedAt,
      detail: `${buffers.carEarlyWarningLabel} · headroom ${num(
        buffers.headroomPp
      ).toFixed(1)} pp`,
    },
  ];
}

export function irrLimits(data: IrrDashboardRead | undefined): LimitRow[] {
  if (!data) return [];
  const metrics = data.metrics;
  return [
    {
      module: 'irr',
      limit: 'Worst ΔEVE / Tier 1',
      value: num(metrics.worstEveChangePctTier1),
      threshold: num(metrics.eveLimitPct),
      direction: 'below',
      status: fromTrafficLight(metrics.eveStatus),
      unit: '%',
      computedAt: data.live?.computedAt,
      detail: `Worst scenario: ${labelize(metrics.worstScenarioCode)}`,
    },
  ];
}

export function fxLimits(data: FxDashboardRead | undefined): LimitRow[] {
  if (!data) return [];
  const metrics = data.metrics;
  const computedAt = data.live?.computedAt;
  const singleLimit = num(metrics.nopSingleLimitPct);
  const rows: LimitRow[] = [
    {
      module: 'fx',
      limit: 'Aggregate NOP / Tier 1',
      value: num(metrics.nopPctTier1),
      threshold: num(metrics.nopAggregateLimitPct),
      direction: 'below',
      status: fromTrafficLight(metrics.nopStatus),
      unit: '%',
      computedAt,
      detail: `Net ${fmtCurrencySigned(num(metrics.nopGhs))}`,
    },
  ];
  for (const position of data.positions) {
    // The largest single currency carries the amber-aware module status; the
    // rest only expose the within-limit boolean.
    const isLargest = position.currency === metrics.singleCcyMaxCurrency;
    rows.push({
      module: 'fx',
      limit: `${position.currency} single-currency position / Tier 1`,
      value: num(position.absPctTier1),
      threshold: singleLimit,
      direction: 'below',
      status: isLargest
        ? fromTrafficLight(metrics.singleCcyStatus)
        : position.withinSingleLimit
        ? 'ok'
        : 'crit',
      unit: '%',
      computedAt,
      detail: `${position.side === 'long' ? 'Long' : 'Short'} ${fmtCurrencySigned(num(position.netGhs))}`,
    });
  }
  return rows;
}

export function ftpLimits(data: FtpDashboardRead | undefined): LimitRow[] {
  if (!data) return [];
  const metrics = data.metrics;
  const computedAt = data.live?.computedAt;
  const rows: LimitRow[] = [
    {
      module: 'ftp',
      limit: 'NMD core share — policy floor',
      value: num(metrics.nmdCorePct),
      threshold: num(metrics.nmdCoreMinPct),
      direction: 'above',
      status: fromTrafficLight(metrics.nmdCoreStatus),
      unit: '%',
      computedAt,
      detail: `Policy band ${num(metrics.nmdCoreMinPct)}–${num(metrics.nmdCoreMaxPct)}%`,
    },
    {
      module: 'ftp',
      limit: 'NMD core share — policy ceiling',
      value: num(metrics.nmdCorePct),
      threshold: num(metrics.nmdCoreMaxPct),
      direction: 'below',
      status: fromTrafficLight(metrics.nmdCoreStatus),
      unit: '%',
      computedAt,
      detail: `Policy band ${num(metrics.nmdCoreMinPct)}–${num(metrics.nmdCoreMaxPct)}%`,
    },
  ];
  if (data.products.length > 0) {
    const worstMargin = Math.min(
      ...data.products.map((product) => num(product.netMarginPct))
    );
    const below = metrics.productsBelowMinMargin;
    rows.push({
      module: 'ftp',
      limit: 'Lowest product net margin — floor',
      value: worstMargin,
      threshold: num(metrics.minProductMarginPct),
      direction: 'above',
      status: below > 0 ? 'crit' : 'ok',
      unit: '%',
      computedAt,
      detail: `${below} of ${metrics.totalProducts} products below the floor`,
    });
  }
  return rows;
}

/**
 * LCR/NSFR rows for the limit wall. The dashboard payload carries statuses
 * but no numeric thresholds, so the thresholds come verbatim from the stored
 * run's snapshotted parameter set (`runThresholds`) — a row is emitted only
 * when its floor exists there; nothing is ever invented client-side.
 */
const RATIO_STATUS: Record<string, LimitStatus> = {
  green: 'ok',
  amber: 'warn',
  red: 'crit',
};

export function liquidityLimits(
  data: LiquidityDashboardRead | undefined,
  run?: RegulatoryRunRead
): LimitRow[] {
  if (!data) return [];
  const thresholds = runThresholds(run);
  const computedAt = data.live?.computedAt;
  const rows: LimitRow[] = [];
  const lcrMin = thresholds['lcr_min'];
  if (lcrMin !== undefined) {
    rows.push({
      module: 'liquidity',
      limit: 'Liquidity Coverage Ratio',
      value: num(data.metrics.lcrPct),
      threshold: lcrMin,
      warnAt: thresholds['lcr_amber_floor'],
      direction: 'above',
      status: RATIO_STATUS[data.metrics.lcrStatus] ?? 'ok',
      unit: '%',
      computedAt,
      detail: '30-day stressed horizon',
    });
  }
  const nsfrMin = thresholds['nsfr_min'];
  if (nsfrMin !== undefined) {
    rows.push({
      module: 'liquidity',
      limit: 'Net Stable Funding Ratio',
      value: num(data.metrics.nsfrPct),
      threshold: nsfrMin,
      warnAt: thresholds['nsfr_amber_floor'],
      direction: 'above',
      status: RATIO_STATUS[data.metrics.nsfrStatus] ?? 'ok',
      unit: '%',
      computedAt,
      detail: '1-year stable funding horizon',
    });
  }
  return rows;
}

export function extractAllLimits(dashboards: {
  liquidity?: LiquidityDashboardRead;
  liquidityRun?: RegulatoryRunRead;
  capital?: CapitalDashboardRead;
  irr?: IrrDashboardRead;
  fx?: FxDashboardRead;
  ftp?: FtpDashboardRead;
}): LimitRow[] {
  return [
    ...liquidityLimits(dashboards.liquidity, dashboards.liquidityRun),
    ...capitalLimits(dashboards.capital),
    ...irrLimits(dashboards.irr),
    ...fxLimits(dashboards.fx),
    ...ftpLimits(dashboards.ftp),
  ];
}
