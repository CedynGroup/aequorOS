'use client';

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
import type { DotProps } from 'recharts';
import {
  CHART_CRIT,
  CHART_GRID,
  axisProps,
  chartMargins,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

export type TrendChartPoint = {
  label: string;
  value: number;
  /** Persisted-run point (solid) vs inline computation (hollow). */
  stored: boolean;
};

/**
 * Period-over-period trend line for FX metrics with an optional limit line.
 * Hollow markers flag points computed inline (no persisted run yet).
 */
export default function TrendChart({
  data,
  threshold,
  thresholdLabel,
  valueLabel,
  format,
  height = 260,
  colorIndex = 0,
  yDomain,
}: {
  data: TrendChartPoint[];
  threshold?: number;
  thresholdLabel?: string;
  valueLabel: string;
  format: (v: number) => string;
  height?: number;
  colorIndex?: number;
  yDomain?: [number, number];
}) {
  const color = seriesColor(colorIndex);

  const renderDot = (props: DotProps & { payload?: TrendChartPoint }) => {
    const { cx, cy, payload } = props;
    if (cx === undefined || cy === undefined) return <g key={`${cx}-${cy}`} />;
    const stored = payload?.stored ?? true;
    return (
      <circle
        key={`${cx}-${cy}`}
        cx={cx}
        cy={cy}
        r={3.5}
        stroke={color}
        strokeWidth={1.5}
        fill={stored ? color : 'rgb(var(--surface-raised))'}
      />
    );
  };

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ ...chartMargins, right: 20 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} />
        <YAxis
          {...axisProps}
          domain={yDomain ?? ['auto', 'auto']}
          tickFormatter={(v: number) => format(v)}
          width={68}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number | string, _name, item) => {
            const point = item?.payload as TrendChartPoint | undefined;
            const v = typeof value === 'number' ? value : Number(value);
            return [
              `${format(v)}${point && !point.stored ? ' · inline' : ''}`,
              valueLabel,
            ];
          }}
        />
        {threshold !== undefined && (
          <ReferenceLine
            y={threshold}
            stroke={CHART_CRIT}
            strokeDasharray="5 4"
            label={
              thresholdLabel
                ? {
                    value: thresholdLabel,
                    position: 'insideTopRight',
                    fontSize: 10,
                    fill: CHART_CRIT,
                  }
                : undefined
            }
          />
        )}
        <Line
          type="monotone"
          dataKey="value"
          stroke={color}
          strokeWidth={2}
          dot={renderDot}
          activeDot={{ r: 5 }}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
