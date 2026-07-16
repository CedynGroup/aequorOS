'use client';

/**
 * Token-themed ratio trend line chart for the liquidity and Basel capital
 * workspaces. A theme-aware copy of components/charts/RatioHistoryChart
 * (which is shared with other modules and therefore left untouched), extended
 * with an optional second series and an explicit red-floor line.
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
  Legend,
} from 'recharts';
import {
  CHART_ACCENT,
  CHART_CRIT,
  CHART_GRID,
  CHART_WARN,
  axisProps,
  chartLegendProps,
  chartMargins,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

export type TrendPoint = {
  label: string;
  primary: number;
  secondary?: number;
  /** false → computed inline (not persisted) — rendered as a hollow point. */
  stored?: boolean;
};

function trendDot(color: string) {
  return function TrendDot(props: unknown) {
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
  };
}

export default function RatioTrendChart({
  data,
  threshold,
  thresholdLabel = 'Min',
  redFloor,
  redFloorLabel = 'Red floor',
  primaryLabel = 'Ratio',
  secondaryLabel,
  yMin,
  yMax,
  height = 260,
}: {
  data: TrendPoint[];
  /** Regulatory minimum (dashed red line). */
  threshold: number;
  thresholdLabel?: string;
  /** Optional lower amber/red boundary (dashed amber line). */
  redFloor?: number;
  redFloorLabel?: string;
  primaryLabel?: string;
  secondaryLabel?: string;
  yMin?: number;
  yMax?: number;
  height?: number;
}) {
  const values = data.flatMap((d) =>
    d.secondary === undefined ? [d.primary] : [d.primary, d.secondary]
  );
  const floors = [threshold, ...(redFloor !== undefined ? [redFloor] : [])];
  const min = yMin ?? Math.floor(Math.min(...values, ...floors) - 5);
  const max = yMax ?? Math.ceil(Math.max(...values, ...floors) + 5);
  const primaryColor = CHART_ACCENT;
  const secondaryColor = seriesColor(1);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ ...chartMargins, right: 24 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} />
        <YAxis
          domain={[min, max]}
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          tickFormatter={(v: number) => `${Math.round(v)}%`}
          width={48}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number, name) => [`${v.toFixed(2)}%`, name]}
        />
        {secondaryLabel && <Legend {...chartLegendProps} verticalAlign="top" align="right" height={24} iconType="line" />}
        <ReferenceLine
          y={threshold}
          stroke={CHART_CRIT}
          strokeDasharray="4 4"
          label={{
            value: `${thresholdLabel} ${threshold}%`,
            position: 'insideBottomRight',
            fill: CHART_CRIT,
            fontSize: 11,
          }}
        />
        {redFloor !== undefined && (
          <ReferenceLine
            y={redFloor}
            stroke={CHART_WARN}
            strokeDasharray="2 4"
            label={{
              value: `${redFloorLabel} ${redFloor}%`,
              position: 'insideBottomLeft',
              fill: CHART_WARN,
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="primary"
          name={primaryLabel}
          stroke={primaryColor}
          strokeWidth={2}
          dot={trendDot(primaryColor)}
          activeDot={{ r: 5 }}
        />
        {secondaryLabel && (
          <Line
            type="monotone"
            dataKey="secondary"
            name={secondaryLabel}
            stroke={secondaryColor}
            strokeWidth={1.5}
            strokeDasharray="5 3"
            dot={trendDot(secondaryColor)}
            activeDot={{ r: 4 }}
          />
        )}
      </LineChart>
    </ResponsiveContainer>
  );
}
