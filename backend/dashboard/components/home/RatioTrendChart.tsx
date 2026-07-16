'use client';

/**
 * Ratio trend — LCR, NSFR and CAR across every reporting period, merged from
 * the liquidity and capital dashboard trend series (per-period values the
 * backend computed from stored baseline runs or inline). LCR/NSFR read the
 * left axis, CAR its own right axis so the ~10–25% capital band stays legible
 * next to triple-digit liquidity ratios.
 */

import { useMemo } from 'react';
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
import type { BankReportingPeriodRead } from '@aequoros/risk-service-api';
import ChartFrame from '@/components/ui/ChartFrame';
import RunBadge from '@/components/ui/RunBadge';
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
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { num } from '@/lib/api/values';

type TrendRow = {
  t: number;
  label: string;
  lcr?: number;
  nsfr?: number;
  car?: number;
};

export default function RatioTrendChart({
  bankId,
  period,
}: {
  bankId: string | undefined;
  period: BankReportingPeriodRead;
}) {
  const liq = useLiquidityDashboard(bankId, period.id);
  const cap = useCapitalDashboard(bankId, period.id);
  // Audit chip for the footer: the liquidity baseline run backing this period.
  const liqRun = useRegulatoryRun(bankId, liq.data?.latestRunId);

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
    return [...byPeriod.values()].sort((a, b) => a.t - b.t);
  }, [liq.data, cap.data]);

  const isLoading = liq.isLoading || cap.isLoading;
  const storedCount = (liq.data?.trend ?? []).filter((p) => p.stored).length;

  return (
    <ChartFrame
      title="Ratio trend"
      subtitle="LCR & NSFR (left axis) · CAR (right axis) per reporting period"
      height={280}
      loading={isLoading}
      footer={
        <>
          <span>
            {rows.length} periods · {storedCount} with stored baseline runs
          </span>
          {liqRun.data && (
            <span className="ml-auto">
              <RunBadge run={liqRun.data} />
            </span>
          )}
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
            <YAxis
              yAxisId="liquidity"
              {...axisProps}
              width={44}
              tickFormatter={(v: number) => `${Math.round(v)}%`}
            />
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
            <Line
              yAxisId="liquidity"
              type="monotone"
              dataKey="lcr"
              name="LCR"
              stroke={seriesColor(0)}
              strokeWidth={1.8}
              dot={false}
              connectNulls
              // The dashboards poll — re-animating every refetch is noise.
              isAnimationActive={false}
            />
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
