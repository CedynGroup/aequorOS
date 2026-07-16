'use client';

/**
 * Stressed-ratio deltas vs the baseline run, per scenario. Negative bars
 * (ratio deterioration) are the expected shape under stress; any positive
 * bar means the shock helped the ratio.
 */

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  Legend,
} from 'recharts';
import {
  CHART_AXIS,
  axisProps,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

export type ScenarioDelta = {
  scenario: string;
  lcrDelta: number | null;
  nsfrDelta: number | null;
};

export default function StressDeltaChart({
  data,
  height = 240,
}: {
  data: ScenarioDelta[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <XAxis dataKey="scenario" {...axisProps} />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          tickFormatter={(v: number) => `${v > 0 ? '+' : ''}${v} pts`}
          width={64}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number, name) => [
            `${v >= 0 ? '+' : ''}${v.toFixed(2)} pts`,
            name,
          ]}
        />
        <Legend {...chartLegendProps} />
        <ReferenceLine y={0} stroke={CHART_AXIS} strokeWidth={1} />
        <Bar
          dataKey="lcrDelta"
          name="ΔLCR vs baseline"
          fill={seriesColor(0)}
          maxBarSize={36}
          radius={[2, 2, 0, 0]}
        />
        <Bar
          dataKey="nsfrDelta"
          name="ΔNSFR vs baseline"
          fill={seriesColor(1)}
          maxBarSize={36}
          radius={[2, 2, 0, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
