'use client';

/**
 * Balance-sheet projection: assets vs liabilities vs equity over the
 * forecast horizon, with an optional base↔adverse range band on total
 * assets when a succeeded adverse-scenario run exists for the same period.
 * Token-themed via lib/chartTheme.ts.
 */

import {
  ResponsiveContainer,
  ComposedChart,
  Area,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
} from 'recharts';
import {
  axisProps,
  CHART_GRID,
  CHART_WARN,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { fmtCurrency } from '@/lib/format';

export type ProjectionPoint = {
  label: string;
  assets: number;
  liabilities: number;
  equity: number;
  /** Adverse-scenario total assets for the same year (band overlay). */
  adverseAssets?: number | null;
  /** [low, high] of base vs adverse assets — the shaded band. */
  band?: [number, number] | null;
};

export default function ProjectionChart({
  data,
  hasBand,
  height = 320,
}: {
  data: ProjectionPoint[];
  hasBand: boolean;
  height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 8, right: 16, left: 4, bottom: 4 }}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="label" {...axisProps} interval={0} />
        <YAxis
          {...axisProps}
          axisLine={false}
          width={64}
          tickFormatter={(v: number) => fmtCurrency(v, undefined, { decimals: 1 })}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number | [number, number], name: string) => {
            if (Array.isArray(v)) {
              return [
                `${fmtCurrency(v[0])} – ${fmtCurrency(v[1])}`,
                name,
              ];
            }
            return [fmtCurrency(v), name];
          }}
        />
        <Legend verticalAlign="top" align="right" height={28} {...chartLegendProps} />
        {hasBand && (
          <Area
            type="monotone"
            dataKey="band"
            stroke="none"
            fill={CHART_WARN}
            fillOpacity={0.12}
            name="Base ↔ adverse assets"
            legendType="none"
            activeDot={false}
            connectNulls
          />
        )}
        <Line
          type="monotone"
          dataKey="assets"
          stroke={seriesColor(0)}
          strokeWidth={2.25}
          name="Total assets"
          dot={{ r: 3, fill: seriesColor(0), strokeWidth: 0 }}
        />
        <Line
          type="monotone"
          dataKey="liabilities"
          stroke={seriesColor(1)}
          strokeWidth={2}
          name="Liabilities"
          dot={{ r: 3, fill: seriesColor(1), strokeWidth: 0 }}
        />
        <Line
          type="monotone"
          dataKey="equity"
          stroke={seriesColor(2)}
          strokeWidth={2}
          name="Equity"
          dot={{ r: 3, fill: seriesColor(2), strokeWidth: 0 }}
        />
        {hasBand && (
          <Line
            type="monotone"
            dataKey="adverseAssets"
            stroke={CHART_WARN}
            strokeWidth={1.5}
            strokeDasharray="5 4"
            name="Adverse assets"
            dot={false}
            connectNulls
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
