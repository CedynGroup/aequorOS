'use client';

/**
 * Cumulative net position per ladder horizon for one currency: contractual
 * (assets − contractual liabilities, cumulated) vs the behaviourally-stressed
 * cumulative from the LRMD ¶50–54 run-off schedule. Categorical series
 * colors only — the blobs carry no statuses, so the chart asserts none.
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
import { fmtCurrency, fmtCurrencySigned } from '@/lib/format';

export type LadderPoint = {
  horizon: string;
  contractual: number | null;
  stressed: number | null;
};

export default function StressedLadderChart({
  data,
  showStressed,
  height = 260,
}: {
  data: LadderPoint[];
  /** Hide the stressed series entirely when the run carries no ladder. */
  showStressed: boolean;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 16, bottom: 4, left: 4 }}>
        <XAxis dataKey="horizon" {...axisProps} />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          tickFormatter={(v: number) => fmtCurrency(v, undefined, { decimals: 0 })}
          width={76}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number, name) => [fmtCurrencySigned(v), name]}
        />
        <Legend {...chartLegendProps} />
        <ReferenceLine y={0} stroke={CHART_AXIS} strokeWidth={1} />
        <Bar
          dataKey="contractual"
          name="Contractual cumulative net"
          fill={seriesColor(0)}
          maxBarSize={40}
          radius={[2, 2, 0, 0]}
        />
        {showStressed && (
          <Bar
            dataKey="stressed"
            name="Behaviourally-stressed cumulative net"
            fill={seriesColor(1)}
            maxBarSize={40}
            radius={[2, 2, 0, 0]}
          />
        )}
      </BarChart>
    </ResponsiveContainer>
  );
}
