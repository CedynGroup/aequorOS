'use client';

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
} from 'recharts';
import { axisProps, chartTooltipProps } from '@/lib/chartTheme';
import { currencyCode } from '@/lib/format';

type Item = {
  level: string;
  shareGHS: number;
  pct: number;
  /** Any CSS color, including chart tokens like 'var(--chart-1)'. */
  color: string;
};

export default function HQLAStackChart({
  data,
  height = 220,
}: {
  data: Item[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        margin={{ top: 8, right: 24, left: 8, bottom: 8 }}
        layout="vertical"
      >
        <XAxis
          type="number"
          {...axisProps}
          tickFormatter={(v) => `${(v / 1_000_000).toFixed(0)}M`}
        />
        <YAxis
          type="category"
          dataKey="level"
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          width={90}
        />
        <Tooltip
          {...chartTooltipProps}
          cursor={{ fill: 'rgb(var(--surface-hover))' }}
          formatter={(v: number) => [`${currencyCode()} ${(v / 1_000_000).toFixed(1)}M`, 'Amount']}
        />
        <Bar dataKey="shareGHS" radius={[0, 2, 2, 0]} maxBarSize={32}>
          {data.map((d, i) => (
            <Cell key={i} fill={d.color} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
