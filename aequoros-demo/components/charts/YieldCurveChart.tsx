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

export type CurvePoint = {
  tenor: string;
  baseYield: number;
  ftpRate: number;
};

/**
 * FTP funding curve: base market yield vs assigned FTP rate across the tenor
 * axis. Two series, percent-valued. Display-only — figures come from the FTP
 * dashboard payload.
 */
export default function YieldCurveChart({
  data,
  height = 300,
}: {
  data: CurvePoint[];
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="tenor"
          axisLine={{ stroke: '#D0D7DE' }}
          tickLine={false}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v: number) => `${v}%`}
          width={48}
          domain={[
            (dataMin: number) => Math.floor(dataMin - 1),
            (dataMax: number) => Math.ceil(dataMax + 1),
          ]}
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
          dataKey="baseYield"
          stroke="#2D7FF9"
          strokeWidth={2}
          name="Base yield"
          dot={{ r: 3, fill: '#2D7FF9' }}
        />
        <Line
          type="monotone"
          dataKey="ftpRate"
          stroke="#0A2540"
          strokeWidth={2}
          name="FTP rate"
          dot={{ r: 3, fill: '#0A2540' }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
