'use client';

/**
 * Tokenized copy of components/charts/RatioHistoryChart for the IRR
 * workspace (the original is shared by other modules and carries hardcoded
 * hex). Colors come from lib/chartTheme so dark and light both work. The
 * threshold here is a ceiling (supervisory ΔEVE/Tier-1 limit), not a floor.
 */

import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import {
  CHART_ACCENT,
  CHART_AXIS,
  CHART_CRIT,
  CHART_GRID,
  chartTooltipProps,
} from '@/lib/chartTheme';

export type TrendPoint = {
  label: string;
  value: number;
  /** false → computed inline (not persisted) — rendered as a hollow point. */
  stored?: boolean;
};

export default function TrendChart({
  data,
  threshold,
  thresholdLabel = 'Limit',
  yMin,
  yMax,
  color = CHART_ACCENT,
  label = 'Ratio',
  height = 260,
}: {
  data: TrendPoint[];
  /** Supervisory ceiling drawn as a critical reference line. */
  threshold?: number;
  thresholdLabel?: string;
  yMin?: number;
  yMax?: number;
  color?: string;
  label?: string;
  height?: number;
}) {
  const values = data.map((d) => d.value);
  const min =
    yMin ?? Math.floor(Math.min(...values, threshold ?? Infinity) - 2);
  const max =
    yMax ?? Math.ceil(Math.max(...values, threshold ?? -Infinity) + 2);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 12, right: 24, left: 0, bottom: 8 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          axisLine={{ stroke: CHART_AXIS }}
          tickLine={false}
          tick={{ fontSize: 11 }}
        />
        <YAxis
          domain={[min, max]}
          axisLine={false}
          tickLine={false}
          tick={{ fontSize: 11 }}
          tickFormatter={(v: number) => `${Math.round(v)}%`}
          width={48}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number) => [`${v.toFixed(2)}%`, label]}
        />
        {threshold !== undefined && (
          <ReferenceLine
            y={threshold}
            stroke={CHART_CRIT}
            strokeDasharray="4 4"
            label={{
              value: `${thresholdLabel} ${threshold}%`,
              position: 'insideTopRight',
              fill: CHART_CRIT,
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          dot={(props) => {
            const { key, cx, cy, payload } = props as {
              key?: string;
              cx?: number;
              cy?: number;
              payload?: TrendPoint;
            };
            const hollow = payload?.stored === false;
            return (
              <circle
                key={key}
                cx={cx}
                cy={cy}
                r={3}
                fill={hollow ? 'rgb(var(--surface-raised))' : color}
                stroke={color}
                strokeWidth={hollow ? 1.5 : 0}
              />
            );
          }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
