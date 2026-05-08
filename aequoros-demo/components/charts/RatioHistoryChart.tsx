'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
} from 'recharts';

export default function RatioHistoryChart({
  data,
  threshold = 100,
  internalBuffer,
  yMin,
  yMax,
  color = '#0E8A4F',
  label = 'Ratio',
}: {
  data: { month: string; value: number }[];
  threshold?: number;
  internalBuffer?: number;
  yMin?: number;
  yMax?: number;
  color?: string;
  label?: string;
}) {
  const min = yMin ?? Math.min(...data.map((d) => d.value), threshold) - 5;
  const max = yMax ?? Math.max(...data.map((d) => d.value)) + 5;

  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="month"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
        />
        <YAxis
          domain={[min, max]}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}%`}
          width={48}
        />
        <Tooltip
          formatter={(v: number) => [`${v.toFixed(1)}%`, label]}
        />
        <ReferenceLine
          y={threshold}
          stroke="#B3261E"
          strokeDasharray="4 4"
          label={{
            value: `Min ${threshold}%`,
            position: 'insideBottomRight',
            fill: '#B3261E',
            fontSize: 11,
          }}
        />
        {internalBuffer && (
          <ReferenceLine
            y={internalBuffer}
            stroke="#C97C00"
            strokeDasharray="2 4"
            label={{
              value: `Buffer ${internalBuffer}%`,
              position: 'insideTopRight',
              fill: '#C97C00',
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          dot={{ r: 3, fill: color }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
