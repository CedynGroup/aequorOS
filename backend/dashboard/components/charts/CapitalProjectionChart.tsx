'use client';

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  ReferenceLine,
  Legend,
} from 'recharts';

export default function CapitalProjectionChart({
  data,
  bogMin = 10,
  internalBuffer = 13.5,
  bufferLabel = 'Buffer',
  criticalFloor,
  height = 300,
}: {
  data: { month: string; car: number; tier1: number }[];
  bogMin?: number;
  internalBuffer?: number;
  bufferLabel?: string;
  /** Optional supervisory-intervention floor (e.g. 9%) rendered below the minimum. */
  criticalFloor?: number;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke="#E4E8EC" strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="month" axisLine={{ stroke: '#D0D7DE' }} tickLine={false} />
        <YAxis
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => `${v}%`}
          width={48}
          domain={[
            (dataMin: number) =>
              Math.floor(Math.min(dataMin, criticalFloor ?? bogMin) - 1),
            (dataMax: number) => Math.ceil(dataMax + 1),
          ]}
        />
        <Tooltip formatter={(v: number, name) => [`${v.toFixed(1)}%`, name]} />
        <Legend
          verticalAlign="top"
          align="right"
          height={28}
          iconType="line"
          wrapperStyle={{ fontSize: '11px' }}
        />
        <ReferenceLine
          y={bogMin}
          stroke="#B3261E"
          strokeDasharray="4 4"
          label={{
            value: `BoG min ${bogMin}%`,
            position: 'insideBottomRight',
            fill: '#B3261E',
            fontSize: 11,
          }}
        />
        <ReferenceLine
          y={internalBuffer}
          stroke="#C97C00"
          strokeDasharray="2 4"
          label={{
            value: `${bufferLabel} ${internalBuffer}%`,
            position: 'insideTopRight',
            fill: '#C97C00',
            fontSize: 11,
          }}
        />
        {criticalFloor !== undefined && (
          <ReferenceLine
            y={criticalFloor}
            stroke="#7A1712"
            strokeDasharray="6 3"
            label={{
              value: `Critical ${criticalFloor}%`,
              position: 'insideBottomLeft',
              fill: '#7A1712',
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="car"
          stroke="#0A2540"
          strokeWidth={2}
          name="CAR"
          dot={{ r: 3, fill: '#0A2540' }}
        />
        <Line
          type="monotone"
          dataKey="tier1"
          stroke="#2D7FF9"
          strokeWidth={2}
          name="Tier 1"
          dot={{ r: 3, fill: '#2D7FF9' }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
