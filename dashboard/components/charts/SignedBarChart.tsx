'use client';

import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  Cell,
  ReferenceLine,
  CartesianGrid,
} from 'recharts';

export type SignedPoint = { label: string; value: number };

/**
 * Bar chart that colours each bar by the sign of its value — used for
 * repricing gaps, ΔEVE by scenario, and net FX positions. Display-only: every
 * value is a backend figure, never recomputed here.
 *
 * `layout="horizontal"` renders vertical columns (category on the x-axis);
 * `layout="vertical"` renders horizontal rows (category on the y-axis).
 */
export default function SignedBarChart({
  data,
  layout = 'horizontal',
  height = 260,
  positiveColor = '#0A2540',
  negativeColor = '#B3261E',
  format = 'ghs-m',
  valueLabel = 'Value',
  categoryWidth = 88,
}: {
  data: SignedPoint[];
  layout?: 'horizontal' | 'vertical';
  height?: number;
  positiveColor?: string;
  negativeColor?: string;
  format?: 'ghs-m' | 'pct';
  valueLabel?: string;
  categoryWidth?: number;
}) {
  const tipFmt = (v: number) =>
    format === 'pct'
      ? `${v.toFixed(2)}%`
      : `GHS ${(v / 1_000_000).toFixed(1)}M`;
  const axisFmt = (v: number) =>
    format === 'pct' ? `${v}%` : `${(v / 1_000_000).toFixed(0)}M`;

  const vertical = layout === 'vertical';

  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        layout={layout}
        margin={{ top: 12, right: 24, left: 8, bottom: 8 }}
      >
        <CartesianGrid
          stroke="#E4E8EC"
          strokeDasharray="3 3"
          horizontal={!vertical}
          vertical={vertical}
        />
        {vertical ? (
          <>
            <XAxis
              type="number"
              axisLine={{ stroke: '#D0D7DE' }}
              tickLine={false}
              tickFormatter={axisFmt}
            />
            <YAxis
              type="category"
              dataKey="label"
              axisLine={false}
              tickLine={false}
              width={categoryWidth}
              tick={{ fontSize: 11 }}
            />
          </>
        ) : (
          <>
            <XAxis
              type="category"
              dataKey="label"
              axisLine={{ stroke: '#D0D7DE' }}
              tickLine={false}
              tick={{ fontSize: 11 }}
              interval={0}
            />
            <YAxis
              type="number"
              axisLine={false}
              tickLine={false}
              tickFormatter={axisFmt}
              width={48}
            />
          </>
        )}
        <Tooltip
          formatter={(v: number) => [tipFmt(v), valueLabel]}
          cursor={{ fill: '#F5F7FA' }}
        />
        <ReferenceLine {...(vertical ? { x: 0 } : { y: 0 })} stroke="#94A3B8" />
        <Bar
          dataKey="value"
          radius={vertical ? [0, 2, 2, 0] : [2, 2, 0, 0]}
          maxBarSize={44}
        >
          {data.map((d, i) => (
            <Cell key={i} fill={d.value >= 0 ? positiveColor : negativeColor} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
