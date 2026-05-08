'use client';

import {
  ResponsiveContainer,
  ComposedChart,
  Bar,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Legend,
  Cell,
} from 'recharts';
import type { GapBucket } from '@/lib/data/irr';

export default function GapAnalysisChart({ data }: { data: GapBucket[] }) {
  const display = data.map((d) => ({
    tenor: d.tenor,
    gapM: d.gap / 1_000_000,
    cumM: d.cumGap / 1_000_000,
  }));

  return (
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={display} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="tenor" axisLine={{ stroke: '#D0D7DE' }} tickLine={false} />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}M`}
          width={56}
        />
        <Tooltip
          formatter={(v: number, name: string) => [`GHS ${v.toFixed(1)}M`, name]}
        />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          iconType="circle"
          wrapperStyle={{ fontSize: '11px' }}
        />
        <ReferenceLine y={0} stroke="#5A6776" strokeWidth={1} />
        <Bar dataKey="gapM" name="Tenor gap" maxBarSize={36} radius={[2, 2, 0, 0]}>
          {display.map((d, i) => (
            <Cell key={i} fill={d.gapM >= 0 ? '#2D7FF9' : '#C97C00'} />
          ))}
        </Bar>
        <Line
          type="monotone"
          dataKey="cumM"
          name="Cumulative gap"
          stroke="#0A2540"
          strokeWidth={2}
          dot={{ r: 3, fill: '#0A2540' }}
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
