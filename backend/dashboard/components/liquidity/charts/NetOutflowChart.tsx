'use client';

/**
 * 30-day net-outflow decomposition: one stacked bar of weighted outflows by
 * category against a second bar of capped inflows, so the net figure the LCR
 * divides by is visible at a glance. All values come straight from the
 * dashboard line items — no client-side regulatory math.
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
  CHART_OK,
  axisProps,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency } from '@/lib/format';

export type OutflowCategory = { name: string; weighted: number };

export default function NetOutflowChart({
  outflows,
  cappedInflows,
  netOutflows,
  height = 220,
}: {
  outflows: OutflowCategory[];
  cappedInflows: number;
  netOutflows: number;
  height?: number;
}) {
  // Two category rows: stacked outflows on top, capped inflows below.
  const outflowRow: Record<string, number | string> = { name: 'Outflows' };
  for (const [i, cat] of outflows.entries()) {
    outflowRow[`c${i}`] = cat.weighted;
  }
  const inflowRow: Record<string, number | string> = {
    name: 'Inflows (capped)',
    inflows: cappedInflows,
  };

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={[outflowRow, inflowRow]}
        layout="vertical"
        margin={{ top: 8, right: 24, bottom: 4, left: 8 }}
      >
        <XAxis
          type="number"
          {...axisProps}
          tickFormatter={(v: number) => fmtCurrency(v, undefined, { decimals: 0 })}
        />
        <YAxis
          type="category"
          dataKey="name"
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          width={104}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number, name) => [fmtCurrency(v), name]}
        />
        <Legend {...chartLegendProps} />
        {outflows.map((cat, i) => (
          <Bar
            key={cat.name}
            dataKey={`c${i}`}
            name={cat.name}
            stackId="flows"
            fill={seriesColor(i)}
            maxBarSize={34}
          />
        ))}
        <Bar
          dataKey="inflows"
          name="Capped inflows"
          stackId="flows"
          fill={CHART_OK}
          maxBarSize={34}
        />
        <ReferenceLine
          x={netOutflows}
          stroke="rgb(var(--text-muted))"
          strokeDasharray="4 3"
          label={{
            value: `Net ${fmtCurrency(netOutflows)}`,
            position: 'top',
            fill: 'rgb(var(--text-muted))',
            fontSize: 11,
          }}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
