'use client';

/**
 * Regulatory-ratio path with a minimum-threshold reference line.
 * Forecasting-local, token-themed copy of components/charts/RatioHistoryChart
 * (that file is shared by other modules and still carries hex colors).
 */

import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ReferenceLine,
  CartesianGrid,
} from 'recharts';
import {
  axisProps,
  CHART_CRIT,
  CHART_GRID,
  CHART_WARN,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

type RatioPoint = {
  label: string;
  value: number;
};

export default function RatioPathChart({
  data,
  threshold = 100,
  thresholdLabel,
  internalBuffer,
  color = seriesColor(0),
  label = 'Ratio',
  height = 240,
}: {
  data: RatioPoint[];
  threshold?: number;
  thresholdLabel?: string;
  internalBuffer?: number;
  /** CSS color string — pass a chartTheme token (seriesColor(n) etc.). */
  color?: string;
  label?: string;
  height?: number;
}) {
  const min = Math.floor(Math.min(...data.map((d) => d.value), threshold) - 2);
  const max = Math.ceil(Math.max(...data.map((d) => d.value)) + 2);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} />
        <YAxis
          {...axisProps}
          axisLine={false}
          domain={[min, max]}
          tickFormatter={(v: number) => `${Math.round(v)}%`}
          width={48}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number) => [`${v.toFixed(2)}%`, label]}
        />
        <ReferenceLine
          y={threshold}
          stroke={CHART_CRIT}
          strokeDasharray="4 4"
          label={{
            value: thresholdLabel ?? `Min ${threshold}%`,
            position: 'insideBottomRight',
            fill: CHART_CRIT,
            fontSize: 11,
          }}
        />
        {internalBuffer !== undefined && (
          <ReferenceLine
            y={internalBuffer}
            stroke={CHART_WARN}
            strokeDasharray="2 4"
            label={{
              value: `Buffer ${internalBuffer}%`,
              position: 'insideTopRight',
              fill: CHART_WARN,
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          name={label}
          dot={{ r: 3, fill: color, strokeWidth: 0 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
