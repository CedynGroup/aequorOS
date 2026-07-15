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

type Item = {
  level: string;
  shareGHS: number;
  pct: number;
  color: string;
};

export default function HQLAStackChart({ data }: { data: Item[] }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart
        data={data}
        margin={{ top: 8, right: 24, left: 8, bottom: 8 }}
        layout="vertical"
      >
        <XAxis
          type="number"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          tickFormatter={(v) => `${(v / 1_000_000).toFixed(0)}M`}
        />
        <YAxis
          type="category"
          dataKey="level"
          axisLine={false}
          tickLine={false}
          width={70}
        />
        <Tooltip
          formatter={(v: number) => [`GHS ${(v / 1_000_000).toFixed(1)}M`, 'Amount']}
          cursor={{ fill: '#F5F7FA' }}
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
