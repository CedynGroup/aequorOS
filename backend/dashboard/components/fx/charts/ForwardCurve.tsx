'use client';

import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_GRID,
  axisProps,
  chartMargins,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

export type ForwardPoint = {
  tenorLabel: string;
  /** Forward outright in GHS per unit of the base currency. */
  outright: number;
};

/**
 * Forward outright curve by tenor. Rendering-only — points come straight
 * from ingested market data once forward tenors exist in the canonical
 * fx_rates store.
 */
export default function ForwardCurve({
  data,
  height = 280,
}: {
  data: ForwardPoint[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ ...chartMargins, right: 20 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="tenorLabel" {...axisProps} />
        <YAxis
          {...axisProps}
          domain={['auto', 'auto']}
          tickFormatter={(v: number) => v.toFixed(2)}
          width={56}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number | string) => [
            Number(value).toFixed(4),
            'Forward outright (GHS)',
          ]}
        />
        <Line
          type="monotone"
          dataKey="outright"
          stroke={seriesColor(0)}
          strokeWidth={2}
          dot={{ r: 3 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
