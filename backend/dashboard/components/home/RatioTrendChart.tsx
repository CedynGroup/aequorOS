'use client';

/**
 * Ratio trend — LCR, NSFR and CAR across every reporting period, merged from
 * the liquidity and capital dashboard trend series (per-period values the
 * backend computed from stored baseline runs or inline). LCR/NSFR read the
 * left axis, CAR its own right axis so the ~10–25% capital band stays legible
 * next to triple-digit liquidity ratios.
 */

import { useMemo, useState } from 'react';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import RangeTabs, { RANGE_MONTHS, type RangePreset } from '@/components/ui/RangeTabs';
import ChartFrame from '@/components/ui/ChartFrame';
import {
  axisProps,
  chartLegendProps,
  chartMargins,
  chartTooltipProps,
  CHART_GRID,
  seriesColor,
} from '@/lib/chartTheme';
import {
  useCapitalDashboard,
  useLiquidityDashboard,
} from '@/lib/api/hooks';
import { num } from '@/lib/api/values';
import { useModuleScope } from '@/components/shell/BankContext';

type TrendRow = {
  t: number;
  label: string;
  lcr?: number;
  nsfr?: number;
  car?: number;
};

export default function RatioTrendChart({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const [range, setRange] = useState<RangePreset>('1Y');
  // An SDI does not file Basel LCR/NSFR (docs/sdi.md §4.6) — its capital headline
  // is the s.29 CAR; liquidity is supervised via LMTD on the Liquidity page.
  const isSdi = useModuleScope().institutionClass === 'sdi';
  // The trend arrays are identical for current and selected-period dashboard
  // reads. Reuse the current payloads already owned by the pulse wall; asking
  // for an explicit period would fetch both heavyweight dashboards again only
  // to render the same home series. Period-specific module pages retain their
  // explicit semantic keys and stored-run behavior.
  const liq = useLiquidityDashboard(bankId);
  const cap = useCapitalDashboard(bankId);

  const rows = useMemo<TrendRow[]>(() => {
    const byPeriod = new Map<string, TrendRow>();
    for (const p of liq.data?.trend ?? []) {
      byPeriod.set(p.reportingPeriodId, {
        t: p.periodEnd.getTime(),
        label: p.label,
        lcr: num(p.lcrPct),
        nsfr: num(p.nsfrPct),
      });
    }
    for (const p of cap.data?.trend ?? []) {
      const existing = byPeriod.get(p.reportingPeriodId);
      if (existing) {
        existing.car = num(p.carPct);
      } else {
        byPeriod.set(p.reportingPeriodId, {
          t: p.periodEnd.getTime(),
          label: p.label,
          car: num(p.carPct),
        });
      }
    }
    const all = [...byPeriod.values()].sort((a, b) => a.t - b.t);
    const months = RANGE_MONTHS[range];
    return months === null ? all : all.slice(-months);
  }, [liq.data, cap.data, range]);

  const isLoading = liq.isLoading || cap.isLoading;
  const windowMove = (() => {
    const withLcr = rows.filter((r) => r.lcr !== undefined);
    if (withLcr.length < 2) return null;
    return (withLcr[withLcr.length - 1].lcr ?? 0) - (withLcr[0].lcr ?? 0);
  })();
  const storedCount = (liq.data?.trend ?? []).filter((p) => p.stored).length;

  return (
    <ChartFrame
      title="Ratio trend"
      subtitle={
        isSdi
          ? 'CAR (s.29) per reporting period'
          : 'LCR & NSFR (left axis) · CAR (right axis) per reporting period'
      }
      height={280}
      loading={isLoading}
      actions={<RangeTabs value={range} onChange={setRange} />}
      footer={
        <>
          <span>
            {rows.length} periods
            {!isSdi && windowMove !== null &&
              ` · LCR ${windowMove >= 0 ? '+' : ''}${windowMove.toFixed(1)}pp over the window`}
            {' · '}
            {storedCount} with stored results
          </span>
        </>
      }
    >
      {rows.length === 0 ? (
        <div className="h-full flex items-center justify-center">
          <p className="text-body text-slate">
            No computed periods yet — activate data in the Data Engine to build
            the history.
          </p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={chartMargins}>
            <CartesianGrid
              stroke={CHART_GRID}
              strokeDasharray="3 3"
              vertical={false}
            />
            <XAxis
              dataKey="label"
              {...axisProps}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            {!isSdi && (
              <YAxis
                yAxisId="liquidity"
                {...axisProps}
                width={44}
                tickFormatter={(v: number) => `${Math.round(v)}%`}
              />
            )}
            <YAxis
              yAxisId="capital"
              orientation="right"
              {...axisProps}
              width={40}
              tickFormatter={(v: number) => `${Math.round(v)}%`}
            />
            <Tooltip
              {...chartTooltipProps}
              formatter={(value: number | string, name: string) => [
                `${num(value).toFixed(2)}%`,
                name,
              ]}
            />
            <Legend {...chartLegendProps} />
            {!isSdi && (
              <Line
                yAxisId="liquidity"
                type="monotone"
                dataKey="lcr"
                name="LCR"
                stroke={seriesColor(0)}
                strokeWidth={1.8}
                dot={false}
                connectNulls
                // Generation invalidation can refresh the series; re-animating
                // a freshness update is noise.
                isAnimationActive={false}
              />
            )}
            {!isSdi && (
              <Line
                yAxisId="liquidity"
                type="monotone"
                dataKey="nsfr"
                name="NSFR"
                stroke={seriesColor(1)}
                strokeWidth={1.8}
                dot={false}
                connectNulls
                isAnimationActive={false}
              />
            )}
            <Line
              yAxisId="capital"
              type="monotone"
              dataKey="car"
              name="CAR"
              stroke={seriesColor(2)}
              strokeWidth={1.8}
              dot={false}
              connectNulls
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </ChartFrame>
  );
}
