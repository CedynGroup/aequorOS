'use client';

import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  ReferenceLine,
} from 'recharts';
import type { BalanceSheetProjection } from '@/lib/data/forecasting';

export default function BalanceSheetProjectionChart({
  data,
  horizonMonths = 36,
}: {
  data: BalanceSheetProjection[];
  horizonMonths?: number;
}) {
  const slice = data.slice(0, horizonMonths + 1);
  return (
    <ResponsiveContainer width="100%" height={340}>
      <AreaChart data={slice} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="month"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          interval={Math.max(1, Math.floor(horizonMonths / 12))}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}M`}
          width={56}
        />
        <Tooltip
          formatter={(v: number, name) => [`GHS ${v.toLocaleString()}M`, name]}
        />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          iconType="circle"
          wrapperStyle={{ fontSize: '11px' }}
        />
        <ReferenceLine x={0} stroke="#0A2540" strokeWidth={1} strokeDasharray="2 2" />
        <Area
          type="monotone"
          dataKey="loans"
          stackId="1"
          stroke="#0A2540"
          fill="#0A2540"
          fillOpacity={0.85}
          name="Loans"
        />
        <Area
          type="monotone"
          dataKey="govSecs"
          stackId="1"
          stroke="#1A4D5C"
          fill="#1A4D5C"
          fillOpacity={0.85}
          name="GoG securities"
        />
        <Area
          type="monotone"
          dataKey="cashAndBoG"
          stackId="1"
          stroke="#2D7FF9"
          fill="#2D7FF9"
          fillOpacity={0.85}
          name="Cash & BoG reserves"
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
