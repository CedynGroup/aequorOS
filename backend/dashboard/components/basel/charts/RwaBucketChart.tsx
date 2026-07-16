'use client';

/**
 * Horizontal RWA bars per exposure class (credit book, standardized
 * approach). Each row is one stored run line item: exposure × risk weight →
 * RWA. Values are read from the run — no client-side weighting.
 */

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
} from 'recharts';
import { axisProps, chartTooltipProps, seriesColor } from '@/lib/chartTheme';
import { fmtCurrency } from '@/lib/format';

export type RwaBucket = {
  name: string;
  rwa: number;
  exposure: number | null;
  weightPct: number | null;
};

export default function RwaBucketChart({
  data,
  height,
}: {
  data: RwaBucket[];
  height?: number;
}) {
  const chartHeight = height ?? Math.max(180, data.length * 34 + 40);
  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 4, right: 24, bottom: 4, left: 8 }}
      >
        <XAxis
          type="number"
          {...axisProps}
          tickFormatter={(v: number) => fmtCurrency(v, 'GHS', { decimals: 0 })}
        />
        <YAxis
          type="category"
          dataKey="name"
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          width={190}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number, _name, item) => {
            const row = item?.payload as RwaBucket | undefined;
            const weight =
              row?.weightPct === null || row?.weightPct === undefined
                ? ''
                : ` @ ${row.weightPct.toFixed(0)}% RW`;
            return [`${fmtCurrency(v, 'GHS')}${weight}`, 'RWA'];
          }}
        />
        <Bar
          dataKey="rwa"
          fill={seriesColor(0)}
          maxBarSize={22}
          radius={[0, 2, 2, 0]}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
