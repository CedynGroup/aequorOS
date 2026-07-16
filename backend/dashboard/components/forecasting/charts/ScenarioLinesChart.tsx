'use client';

/**
 * Generic multi-series line comparison over the forecast horizon — powers
 * run-vs-run comparisons, base-vs-shocked what-if overlays, and the NII
 * scenario chart. Token-themed via lib/chartTheme.ts.
 */

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
import {
  axisProps,
  CHART_CRIT,
  CHART_GRID,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

export type ScenarioSeries = {
  /** Data key on each point. */
  key: string;
  name: string;
  /** Categorical palette index (lib/chartTheme.ts). */
  colorIndex?: number;
  /** Explicit CSS color token — overrides colorIndex. */
  color?: string;
  dashed?: boolean;
};

export type ScenarioPoint = { label: string } & Record<
  string,
  string | number | null
>;

export default function ScenarioLinesChart({
  data,
  series,
  valueFormatter,
  tickFormatter,
  threshold,
  thresholdLabel,
  height = 280,
  yDomain,
}: {
  data: ScenarioPoint[];
  series: ScenarioSeries[];
  valueFormatter: (v: number) => string;
  tickFormatter?: (v: number) => string;
  threshold?: number;
  thresholdLabel?: string;
  height?: number;
  yDomain?: [number | 'auto' | 'dataMin', number | 'auto' | 'dataMax'];
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis
          {...axisProps}
          axisLine={false}
          width={60}
          domain={yDomain}
          tickFormatter={tickFormatter ?? ((v: number) => valueFormatter(v))}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number, name: string) => [valueFormatter(v), name]}
        />
        <Legend verticalAlign="top" align="right" height={28} {...chartLegendProps} />
        {threshold !== undefined && (
          <ReferenceLine
            y={threshold}
            stroke={CHART_CRIT}
            strokeDasharray="4 4"
            label={{
              value: thresholdLabel ?? `Min ${threshold}`,
              position: 'insideBottomRight',
              fill: CHART_CRIT,
              fontSize: 11,
            }}
          />
        )}
        {series.map((s, i) => {
          const stroke = s.color ?? seriesColor(s.colorIndex ?? i);
          return (
            <Line
              key={s.key}
              type="monotone"
              dataKey={s.key}
              stroke={stroke}
              strokeWidth={2}
              strokeDasharray={s.dashed ? '5 4' : undefined}
              name={s.name}
              dot={{ r: 3, fill: stroke, strokeWidth: 0 }}
              connectNulls
            />
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}
