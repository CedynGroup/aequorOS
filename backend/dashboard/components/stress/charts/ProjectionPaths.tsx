'use client';

/**
 * Base-vs-stress ratio projection path with the regulatory floor (docs/stress.md
 * §4 item 3 — "ratio-vs-threshold paths ... base vs stress"). Reuses the shared
 * multi-series `ScenarioLinesChart`; reads the immutable run's per-year
 * projection (`projection.{current,base[],stress[]}`).
 */

import ScenarioLinesChart, {
  type ScenarioPoint,
  type ScenarioSeries,
} from '@/components/forecasting/charts/ScenarioLinesChart';
import { num } from '@/lib/api/values';
import type { EnterpriseProjection, ProjectionYear } from '../types';

export type RatioMetricKey =
  | 'car_pct'
  | 'cet1_ratio_pct'
  | 'tier1_ratio_pct'
  | 'leverage_ratio_pct'
  | 'lcr_pct'
  | 'nsfr_pct';

function yearLabel(y: ProjectionYear): string {
  return y.leg === 'current' ? 'Now' : `Y${y.year}`;
}

export default function ProjectionPaths({
  projection,
  metricKey,
  threshold,
  thresholdLabel,
  height = 240,
}: {
  projection: EnterpriseProjection;
  metricKey: RatioMetricKey;
  threshold?: number;
  thresholdLabel?: string;
  height?: number;
}) {
  const current = projection.current;
  const data: ScenarioPoint[] = [
    { label: 'Now', base: num(current[metricKey]), stress: num(current[metricKey]) },
    ...projection.base.map((b, i) => {
      const s = projection.stress[i];
      return {
        label: yearLabel(b),
        base: num(b[metricKey]),
        stress: s ? num(s[metricKey]) : null,
      } as ScenarioPoint;
    }),
  ];

  const series: ScenarioSeries[] = [
    { key: 'base', name: 'Base case', colorIndex: 1 },
    { key: 'stress', name: 'Stress', colorIndex: 4, dashed: true },
  ];

  return (
    <ScenarioLinesChart
      data={data}
      series={series}
      valueFormatter={(v) => `${v.toFixed(2)}%`}
      tickFormatter={(v) => `${v.toFixed(0)}%`}
      threshold={threshold}
      thresholdLabel={thresholdLabel}
      height={height}
    />
  );
}
