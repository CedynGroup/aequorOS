'use client';

/**
 * Earnings bridge over the forecast horizon — stacked income bars
 * (NII + fees) with opex and credit losses as negative bars and net income
 * as a line. Every series is a persisted field on the projection path.
 * Token-themed via lib/chartTheme.ts.
 */

import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  ReferenceLine,
} from 'recharts';
import {
  axisProps,
  CHART_AXIS,
  CHART_CRIT,
  CHART_GRID,
  CHART_OK,
  CHART_WARN,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

export type EarningsPoint = {
  label: string;
  nii: number;
  fees: number;
  /** Negative values (costs drawn below the axis). */
  opex: number;
  creditLosses: number;
  netIncome: number;
};

export default function EarningsChart({
  data,
  height = 320,
}: {
  data: EarningsPoint[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={data}
        stackOffset="sign"
        margin={{ top: 8, right: 16, left: 4, bottom: 4 }}
      >
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis
          {...axisProps}
          axisLine={false}
          width={64}
          tickFormatter={(v: number) => fmtCurrency(v, 'GHS', { decimals: 1 })}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number, name: string) => [fmtCurrencySigned(v, 'GHS'), name]}
        />
        <Legend verticalAlign="top" align="right" height={28} {...chartLegendProps} />
        <ReferenceLine y={0} stroke={CHART_AXIS} />
        <Bar
          dataKey="nii"
          stackId="pl"
          fill={seriesColor(0)}
          name="Net interest income"
          maxBarSize={42}
        />
        <Bar
          dataKey="fees"
          stackId="pl"
          fill={seriesColor(2)}
          name="Fee income"
          maxBarSize={42}
        />
        <Bar
          dataKey="opex"
          stackId="pl"
          fill={CHART_WARN}
          fillOpacity={0.75}
          name="Operating expenses"
          maxBarSize={42}
        />
        <Bar
          dataKey="creditLosses"
          stackId="pl"
          fill={CHART_CRIT}
          fillOpacity={0.75}
          name="Credit losses"
          maxBarSize={42}
        />
        <Line
          type="monotone"
          dataKey="netIncome"
          stroke={CHART_OK}
          strokeWidth={2.25}
          name="Net income"
          dot={{ r: 3, fill: CHART_OK, strokeWidth: 0 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
