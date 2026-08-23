'use client';

/**
 * Base-vs-stress ratio projection path with the regulatory floor (docs/stress.md
 * §4 item 3 — "ratio-vs-threshold paths ... base vs stress"). Reuses the shared
 * multi-series `ScenarioLinesChart`; reads the immutable run's per-year
 * projection (`projection.{current,base[],stress[]}`).
 *
 * A year the engine did not compute is plotted as a GAP, never as 0%. Under
 * `num()` a null CAR/LCR year became a 0% point on the line — a ratio at zero
 * is the worst reading the chart can show, and it is not a reading at all.
 */

import ScenarioLinesChart, {
  type ScenarioPoint,
  type ScenarioSeries,
} from '@/components/forecasting/charts/ScenarioLinesChart';
import { numOrNull } from '@/lib/api/values';
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
  /**
   * The regulatory floor, from the run payload. A non-positive or non-finite
   * value means "not configured" and draws NO floor line: a reference line at
   * 0% is not a floor, it is a picture of unlimited headroom.
   */
  threshold?: number | null;
  thresholdLabel?: string;
  height?: number;
}) {
  const floor =
    typeof threshold === 'number' && Number.isFinite(threshold) && threshold > 0
      ? threshold
      : undefined;
  const current = projection.current;
  const currentValue = numOrNull(current[metricKey]);
  const data: ScenarioPoint[] = [
    { label: 'Now', base: currentValue, stress: currentValue },
    ...projection.base.map((b, i) => {
      const s = projection.stress[i];
      return {
        label: yearLabel(b),
        base: numOrNull(b[metricKey]),
        stress: s ? numOrNull(s[metricKey]) : null,
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
      threshold={floor}
      thresholdLabel={floor === undefined ? undefined : thresholdLabel}
      height={height}
    />
  );
}
