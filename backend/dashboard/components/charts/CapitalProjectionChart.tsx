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
import {
  CHART_ACCENT,
  CHART_CRIT,
  CHART_GRID,
  CHART_WARN,
  axisProps,
  chartLegendProps,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';

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
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="month" {...axisProps} />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={axisProps.tick}
          tickFormatter={(v) => `${v}%`}
          width={48}
          domain={[
            (dataMin: number) =>
              Math.floor(Math.min(dataMin, criticalFloor ?? bogMin) - 1),
            (dataMax: number) => Math.ceil(dataMax + 1),
          ]}
        />
        <Tooltip
          {...chartTooltipProps}
          formatter={(v: number, name) => [`${v.toFixed(1)}%`, name]}
        />
        <Legend
          {...chartLegendProps}
          verticalAlign="top"
          align="right"
          height={28}
          iconType="line"
        />
        <ReferenceLine
          y={bogMin}
          stroke={CHART_CRIT}
          strokeDasharray="4 4"
          label={{
            value: `BoG min ${bogMin}%`,
            position: 'insideBottomRight',
            fill: CHART_CRIT,
            fontSize: 11,
          }}
        />
        <ReferenceLine
          y={internalBuffer}
          stroke={CHART_WARN}
          strokeDasharray="2 4"
          label={{
            value: `${bufferLabel} ${internalBuffer}%`,
            position: 'insideTopRight',
            fill: CHART_WARN,
            fontSize: 11,
          }}
        />
        {criticalFloor !== undefined && (
          <ReferenceLine
            y={criticalFloor}
            stroke={CHART_CRIT}
            strokeDasharray="6 3"
            strokeOpacity={0.7}
            label={{
              value: `Critical ${criticalFloor}%`,
              position: 'insideBottomLeft',
              fill: CHART_CRIT,
              fontSize: 11,
            }}
          />
        )}
        <Line
          type="monotone"
          dataKey="car"
          stroke={CHART_ACCENT}
          strokeWidth={2}
          name="CAR"
          dot={{ r: 3, fill: CHART_ACCENT }}
        />
        <Line
          type="monotone"
          dataKey="tier1"
          stroke={seriesColor(1)}
          strokeWidth={2}
          name="Tier 1"
          dot={{ r: 3, fill: seriesColor(1) }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
