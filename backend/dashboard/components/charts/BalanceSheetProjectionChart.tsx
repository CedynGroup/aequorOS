'use client';

/**
 * Stacked asset-composition projection (loans / securities / cash).
 * Used exclusively by the Balance Sheet Forecasting workspace.
 * Token-themed via lib/chartTheme.ts — follows the active dark/light theme.
 */

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';
import {
  axisProps,
  CHART_GRID,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

export type BalanceSheetPoint = {
  /** X-axis label (e.g. "Y0", "2027-03"). */
  month: string;
  /** GHS millions. */
  loans: number;
  securities: number;
  cash: number;
};

export default function BalanceSheetProjectionChart({
  data,
  height = 340,
}: {
  data: BalanceSheetPoint[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <AreaChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="month" {...axisProps} interval={0} />
        <YAxis
          {...axisProps}
          axisLine={false}
          tickFormatter={(v: number) => `${v.toLocaleString()}M`}
          width={56}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number, name: string) => [
            `GHS ${v.toLocaleString()}M`,
            name,
          ]}
        />
        <Legend verticalAlign="top" align="right" height={28} {...chartLegendProps} />
        <Area
          type="monotone"
          dataKey="loans"
          stackId="1"
          stroke={seriesColor(0)}
          fill={seriesColor(0)}
          fillOpacity={0.8}
          name="Loans"
        />
        <Area
          type="monotone"
          dataKey="securities"
          stackId="1"
          stroke={seriesColor(1)}
          fill={seriesColor(1)}
          fillOpacity={0.8}
          name="Securities"
        />
        <Area
          type="monotone"
          dataKey="cash"
          stackId="1"
          stroke={seriesColor(2)}
          fill={seriesColor(2)}
          fillOpacity={0.8}
          name="Cash & BoG reserves"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
