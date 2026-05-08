'use client';

import {
  ResponsiveContainer,
  ComposedChart,
  Line,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Legend,
} from 'recharts';

export default function FxRateChart({
  data,
}: {
  data: {
    day: number;
    actual: number | null;
    predicted: number;
    upper: number;
    lower: number;
    forward: number;
  }[];
}) {
  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart data={data} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="day"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          tickFormatter={(v) => (v === 0 ? 'Today' : `D${v >= 0 ? '+' : ''}${v}`)}
          interval={20}
        />
        <YAxis
          domain={['auto', 'auto']}
          axisLine={false}
          tickLine={false}
          width={56}
          tickFormatter={(v) => v.toFixed(2)}
        />
        <Tooltip
          formatter={(v: number, name: string) => [v?.toFixed(3) ?? '-', name]}
          labelFormatter={(v) => (v === 0 ? 'Today' : `Day ${v >= 0 ? '+' : ''}${v}`)}
        />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          iconType="line"
          wrapperStyle={{ fontSize: '11px' }}
        />
        <ReferenceLine x={0} stroke="#0A2540" strokeWidth={1} strokeDasharray="2 2" />
        <Area
          type="monotone"
          dataKey="upper"
          stroke="none"
          fill="#2D7FF9"
          fillOpacity={0.08}
          legendType="none"
          name="95% upper"
        />
        <Area
          type="monotone"
          dataKey="lower"
          stroke="none"
          fill="#FFFFFF"
          fillOpacity={1}
          legendType="none"
          name="95% lower"
        />
        <Line
          type="monotone"
          dataKey="actual"
          stroke="#0A2540"
          strokeWidth={2}
          dot={false}
          name="Actual"
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="predicted"
          stroke="#2D7FF9"
          strokeWidth={2}
          strokeDasharray="0"
          dot={false}
          name="ML forecast"
        />
        <Line
          type="monotone"
          dataKey="forward"
          stroke="#5A6776"
          strokeWidth={1.5}
          strokeDasharray="4 3"
          dot={false}
          name="Forward implied"
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
