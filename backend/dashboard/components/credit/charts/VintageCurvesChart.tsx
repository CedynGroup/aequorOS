'use client';

/**
 * Cohort PAR30+ curves: x = months on book, one line per cohort. The four
 * most recent cohorts take the series palette; older cohorts render as thin
 * grid-toned context lines. Holes in a cohort's observation history stay
 * holes (connectNulls off) — never interpolated.
 */

import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { VintageCohortRead } from '@aequoros/risk-service-api';
import {
  CHART_GRID,
  axisProps,
  chartLegendProps,
  chartMargins,
  chartTooltipProps,
  seriesColor,
} from '@/lib/chartTheme';
import { num } from '@/lib/api/values';

export default function VintageCurvesChart({
  cohorts,
  height = 300,
}: {
  cohorts: VintageCohortRead[];
  height?: number;
}) {
  const maxAge = Math.max(
    0,
    ...cohorts.flatMap((cohort) => cohort.points.map((point) => point.monthsOnBook))
  );
  const rows: Record<string, number | null | string>[] = [];
  for (let age = 0; age <= maxAge; age += 1) {
    const row: Record<string, number | null | string> = { monthsOnBook: age };
    for (const cohort of cohorts) {
      const point = cohort.points.find((p) => p.monthsOnBook === age);
      row[cohort.cohort] = point ? num(point.par30Pct) : null;
    }
    rows.push(row);
  }
  const recent = cohorts.slice(-4).map((cohort) => cohort.cohort);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={chartMargins}>
        <CartesianGrid stroke={CHART_GRID} strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="monthsOnBook"
          type="number"
          domain={[0, maxAge]}
          allowDecimals={false}
          {...axisProps}
        />
        <YAxis {...axisProps} tickFormatter={(value: number) => `${value.toFixed(0)}%`} width={48} />
        <Tooltip
          {...chartTooltipProps}
          formatter={(value: number | string, name: string) => [
            `${Number(value).toFixed(2)}%`,
            name,
          ]}
          labelFormatter={(age) => `Month ${age} on book`}
        />
        <Legend {...chartLegendProps} />
        {cohorts.map((cohort) => {
          const highlightIndex = recent.indexOf(cohort.cohort);
          const highlighted = highlightIndex >= 0;
          return (
            <Line
              key={cohort.cohort}
              dataKey={cohort.cohort}
              type="monotone"
              connectNulls={false}
              dot={false}
              strokeWidth={highlighted ? 2 : 1}
              stroke={highlighted ? seriesColor(highlightIndex) : CHART_GRID}
              legendType={highlighted ? 'line' : 'none'}
            />
          );
        })}
      </LineChart>
    </ResponsiveContainer>
  );
}
