'use client';

/**
 * Multi-year capital ratio projection (CAR / Tier 1 / CET1) against the BoG
 * CAR floors. Feeds from stored forecast-run projection years — the chart
 * does no projection math of its own.
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
import { regShort } from '@/lib/format';

export type PlanPoint = {
  label: string;
  car: number;
  tier1: number;
  cet1: number;
};

export default function CapitalPlanChart({
  data,
  carMin,
  earlyWarning,
  earlyWarningLabel = 'Early warning',
  height = 300,
}: {
  data: PlanPoint[];
  carMin: number;
  earlyWarning?: number;
  earlyWarningLabel?: string;
  height?: number;
}) {
  const values = data.flatMap((d) => [d.car, d.tier1, d.cet1]);
  const min = Math.floor(Math.min(...values, carMin) - 1.5);
  const max = Math.ceil(Math.max(...values, earlyWarning ?? carMin) + 1.5);

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
          tickFormatter={(v: number) => `${v}%`}
          width={44}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number, name) => [`${v.toFixed(2)}%`, name]}
        />
        <Legend {...chartLegendProps} verticalAlign="top" align="right" height={24} iconType="line" />
        <ReferenceLine
          y={carMin}
          stroke={CHART_CRIT}
          strokeDasharray="4 4"
          label={{
            value: `${regShort()} min ${carMin}%`,
            position: 'insideBottomRight',
            fill: CHART_CRIT,
            fontSize: 11,
          }}
        />
        {earlyWarning !== undefined && (
          <ReferenceLine
            y={earlyWarning}
            stroke={CHART_WARN}
            strokeDasharray="2 4"
            label={{
              value: `${earlyWarningLabel} ${earlyWarning}%`,
              position: 'insideTopRight',
              fill: CHART_WARN,
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="car"
          name="CAR"
          stroke={CHART_ACCENT}
          strokeWidth={2}
          dot={{ r: 3 }}
          activeDot={{ r: 5 }}
        />
        <Line
          type="monotone"
          dataKey="tier1"
          name="Tier 1"
          stroke={seriesColor(1)}
          strokeWidth={1.5}
          dot={{ r: 2.5 }}
        />
        <Line
          type="monotone"
          dataKey="cet1"
          name="CET1"
          stroke={seriesColor(2)}
          strokeWidth={1.5}
          strokeDasharray="5 3"
          dot={{ r: 2.5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
