'use client';

import { ResponsiveContainer, PieChart, Pie, Cell, Tooltip } from 'recharts';
import { chartTooltipProps } from '@/lib/chartTheme';

export type DonutSlice = {
  name: string;
  value: number;
  /** Any CSS color, including chart tokens like 'var(--chart-1)'. */
  color: string;
};

const formatters: Record<string, (v: number) => string> = {
  percent: (v) => `${v}%`,
  raw: (v) => v.toString(),
  'ghs-m': (v) => `GHS ${v.toFixed(1)}M`,
};

export default function DonutChart({
  data,
  centerLabel,
  centerValue,
  height = 240,
  format = 'raw',
}: {
  data: DonutSlice[];
  centerLabel?: string;
  centerValue?: string;
  height?: number;
  format?: 'percent' | 'raw' | 'ghs-m';
}) {
  const formatter = formatters[format] ?? formatters.raw;
  return (
    <div className="relative">
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie
            data={data}
            innerRadius="62%"
            outerRadius="92%"
            paddingAngle={1}
            dataKey="value"
            startAngle={90}
            endAngle={-270}
            stroke="rgb(var(--surface-raised))"
            strokeWidth={2}
          >
            {data.map((s, i) => (
              <Cell key={i} fill={s.color} />
            ))}
          </Pie>
          <Tooltip
            {...chartTooltipProps}
            cursor={false}
            formatter={(v: number, name) => [formatter(v), name]}
          />
        </PieChart>
      </ResponsiveContainer>
      {(centerLabel || centerValue) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
          {centerLabel && (
            <p className="text-micro font-medium uppercase tracking-wider text-slate">
              {centerLabel}
            </p>
          )}
          {centerValue && (
            <p className="font-mono text-h1 font-semibold text-navy tabular-nums mt-0.5">
              {centerValue}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
