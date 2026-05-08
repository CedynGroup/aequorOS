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
import type { CashFlowPoint } from '@/lib/data/liquidity';

export default function CashFlowForecastChart({
  data,
  showStatic = true,
  horizonDays = 90,
}: {
  data: CashFlowPoint[];
  showStatic?: boolean;
  horizonDays?: 30 | 60 | 90;
}) {
  const sliced = data.slice(0, horizonDays);

  return (
    <ResponsiveContainer width="100%" height={340}>
      <ComposedChart
        data={sliced}
        margin={{ top: 16, right: 24, left: 0, bottom: 8 }}
      >
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="day"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          tickFormatter={(v) => `D+${v}`}
          interval={Math.floor(horizonDays / 12)}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}M`}
          width={50}
        />
        <Tooltip
          labelFormatter={(v) => `Day +${v}`}
          formatter={(value: number, name: string) => [
            `GHS ${value.toFixed(2)}M`,
            name,
          ]}
        />
        <Legend
          verticalAlign="top"
          align="right"
          iconType="line"
          height={28}
          wrapperStyle={{ fontSize: '11px', paddingBottom: '8px' }}
        />
        <ReferenceLine y={0} stroke="#5A6776" strokeWidth={1} />

        {/* LSTM confidence band */}
        <Area
          type="monotone"
          dataKey="upper"
          stroke="none"
          fill="#2D7FF9"
          fillOpacity={0.08}
          name="LSTM 95% upper"
          legendType="none"
        />
        <Area
          type="monotone"
          dataKey="lower"
          stroke="none"
          fill="#FFFFFF"
          fillOpacity={1}
          name="LSTM 95% lower"
          legendType="none"
        />

        {showStatic && (
          <Line
            type="monotone"
            dataKey="netStatic"
            stroke="#5A6776"
            strokeDasharray="4 3"
            strokeWidth={1.5}
            dot={false}
            name="Static behavioral"
          />
        )}
        <Line
          type="monotone"
          dataKey="netLstm"
          stroke="#2D7FF9"
          strokeWidth={2}
          dot={false}
          name="LSTM forecast"
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
