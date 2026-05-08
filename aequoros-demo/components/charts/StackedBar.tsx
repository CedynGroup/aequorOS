'use client';

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Cell,
} from 'recharts';

export default function StackedBar({
  data,
  xKey,
  yKey,
  colorKey,
  height = 240,
  yFormatter,
}: {
  data: Record<string, unknown>[];
  xKey: string;
  yKey: string;
  colorKey?: string;
  height?: number;
  yFormatter?: (v: number) => string;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey={xKey}
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          width={50}
          tickFormatter={yFormatter}
        />
        <Tooltip
          formatter={(v: number) => yFormatter ? yFormatter(v) : v.toString()}
          cursor={{ fill: '#F5F7FA' }}
        />
        <Bar dataKey={yKey} radius={[2, 2, 0, 0]} maxBarSize={48}>
          {data.map((d, i) => (
            <Cell
              key={i}
              fill={(colorKey && (d[colorKey] as string)) ?? '#2D7FF9'}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
