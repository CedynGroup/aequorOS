'use client';

import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_ACCENT,
  CHART_AXIS,
  CHART_GRID,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

export type LadderBucket = {
  bucket: string;
  /** Rate-sensitive assets (GHS, backend figure). */
  rsa: number;
  /** Rate-sensitive liabilities (GHS, backend figure) — plotted downward. */
  rsl: number;
  /** Period gap RSA − RSL (GHS, backend figure). */
  gap: number;
  /** Running cumulative gap (GHS, backend figure). */
  cumulative: number;
};

/**
 * Repricing ladder: per-bucket grouped bars (RSA up, RSL down, net gap) with
 * the backend's cumulative gap as a line overlay. `mini` collapses to net gap
 * + cumulative for dashboard tiles. All series are engine outputs — RSL is
 * only negated for the mirror presentation.
 */
export default function RepricingLadderChart({
  data,
  height = 320,
  mini = false,
}: {
  data: LadderBucket[];
  height?: number;
  mini?: boolean;
}) {
  const rows = data.map((d) => ({
    ...d,
    rslDown: -d.rsl,
  }));

  const axisFmt = (v: number) => `${(v / 1_000_000).toFixed(0)}M`;

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart
        data={rows}
        margin={{ top: 8, right: 16, left: 8, bottom: 4 }}
      >
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="bucket"
          axisLine={{ stroke: CHART_AXIS }}
          tickLine={false}
          tick={{ fontSize: 11 }}
          interval={0}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={{ fontSize: 11 }}
          tickFormatter={axisFmt}
          width={52}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number, name: string) => {
            if (name === 'RSL') return [fmtCurrency(Math.abs(v)), name];
            if (name === 'RSA') return [fmtCurrency(v), name];
            return [fmtCurrencySigned(v), name];
          }}
        />
        {!mini && <Legend {...chartLegendProps} />}
        <ReferenceLine y={0} stroke={CHART_AXIS} />
        {!mini && (
          <Bar
            dataKey="rsa"
            name="RSA"
            fill={seriesColor(0)}
            radius={[2, 2, 0, 0]}
            maxBarSize={26}
          />
        )}
        {!mini && (
          <Bar
            dataKey="rslDown"
            name="RSL"
            fill={seriesColor(1)}
            radius={[0, 0, 2, 2]}
            maxBarSize={26}
          />
        )}
        <Bar
          dataKey="gap"
          name="Net gap"
          fill={seriesColor(mini ? 0 : 2)}
          radius={[2, 2, 0, 0]}
          maxBarSize={26}
        />
        <Line
          type="monotone"
          dataKey="cumulative"
          name="Cumulative gap"
          stroke={CHART_ACCENT}
          strokeWidth={2}
          dot={{ r: 2.5 }}
          activeDot={{ r: 4 }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
