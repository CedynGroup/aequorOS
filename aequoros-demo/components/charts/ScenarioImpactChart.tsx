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
  ReferenceLine,
  Legend,
} from 'recharts';

export default function ScenarioImpactChart({
  data,
  metric,
}: {
  data: { name: string; impact: number }[];
  metric: 'NII' | 'EVE';
}) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <BarChart
        data={data}
        layout="vertical"
        margin={{ top: 8, right: 24, left: 0, bottom: 8 }}
      >
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" horizontal={false} />
        <XAxis
          type="number"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          tickFormatter={(v) => `${v.toFixed(0)}M`}
        />
        <YAxis
          dataKey="name"
          type="category"
          axisLine={false}
          tickLine={false}
          width={140}
        />
        <Tooltip
          formatter={(v: number) => [
            `GHS ${v >= 0 ? '+' : ''}${v.toFixed(1)}M`,
            `${metric} impact`,
          ]}
        />
        <ReferenceLine x={0} stroke="#5A6776" strokeWidth={1} />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          wrapperStyle={{ fontSize: '11px' }}
        />
        <Bar
          dataKey="impact"
          name={`${metric} impact`}
          maxBarSize={28}
          radius={[0, 2, 2, 0]}
        >
          {data.map((d, i) => (
            <Cell key={i} fill={d.impact >= 0 ? '#0E8A4F' : '#B3261E'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
