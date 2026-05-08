'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';
import type { CurvePoint } from '@/lib/data/ftp';

export default function YieldCurveChart({ data }: { data: CurvePoint[] }) {
  return (
    <ResponsiveContainer width="100%" height={300}>
      <LineChart data={data} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="tenor"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}%`}
          width={48}
        />
        <Tooltip formatter={(v: number, name) => [`${v.toFixed(2)}%`, name]} />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          iconType="line"
          wrapperStyle={{ fontSize: '11px' }}
        />
        <Line
          type="monotone"
          dataKey="lending"
          stroke="#0E8A4F"
          strokeWidth={2}
          name="Lending curve"
          dot={{ r: 3, fill: '#0E8A4F' }}
        />
        <Line
          type="monotone"
          dataKey="ftp"
          stroke="#0A2540"
          strokeWidth={2}
          name="FTP curve"
          dot={{ r: 3, fill: '#0A2540' }}
        />
        <Line
          type="monotone"
          dataKey="bog"
          stroke="#2D7FF9"
          strokeWidth={2}
          name="BoG / Market curve"
          dot={{ r: 3, fill: '#2D7FF9' }}
          strokeDasharray="0"
        />
        <Line
          type="monotone"
          dataKey="deposit"
          stroke="#C97C00"
          strokeWidth={2}
          name="Deposit curve"
          dot={{ r: 3, fill: '#C97C00' }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
