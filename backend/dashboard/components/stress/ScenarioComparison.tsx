'use client';

/**
 * N-way scenario comparison (docs/stress.md §4 item 6) — a scenario × metric
 * matrix, not a two-run A/B. Leads with the severity-ladder heatmap (rows sorted
 * worst-CAR-erosion first) so the board reads the whole selected scenario set at
 * a glance; clicking a row focuses that run for driver drill-down.
 */

import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ScenarioHeatmap, { type HeatColumn, type HeatRow } from './charts/ScenarioHeatmap';
import { num } from '@/lib/api/values';
import type { EnterpriseStressRead } from './types';

const CAR_FLOOR = 13;
const LCR_FLOOR = 100;

/** Map a floor-type ratio to a 0..1 intensity (0 safe … 1 at/below floor). */
function floorIntensity(value: number, floor: number): { intensity: number; breach: boolean } {
  const headroom = value - floor;
  const band = floor * 0.5;
  const intensity = Math.max(0, Math.min(1, 1 - headroom / band));
  return { intensity, breach: value < floor };
}

const COLUMNS: HeatColumn[] = [
  { key: 'car', label: 'Stressed CAR', sub: 'floor 13%' },
  { key: 'erosion', label: 'CAR erosion', sub: 'pp' },
  { key: 'cet1', label: 'Stressed CET1', sub: '' },
  { key: 'lcr', label: 'Stressed LCR', sub: 'floor 100%' },
  { key: 'gap', label: 'Capital gap', sub: "GHS'000" },
];

export default function ScenarioComparison({
  runs,
  focusedRunId,
  onFocus,
}: {
  runs: EnterpriseStressRead[];
  focusedRunId?: string;
  onFocus: (run: EnterpriseStressRead) => void;
}) {
  if (runs.length === 0) return null;

  const rows: HeatRow[] = [...runs]
    .sort((a, b) => num(b.summary.car_erosion_pp) - num(a.summary.car_erosion_pp))
    .map((run) => {
      const car = num(run.summary.stressed_car_end_pct);
      const erosion = num(run.summary.car_erosion_pp);
      // An SDI run has no Basel LCR (docs/sdi.md §4.6) — show n/a, never a false 0% breach.
      const lcrAssessed = run.summary.stressed_lcr_pct !== null;
      const lcr = num(run.summary.stressed_lcr_pct);
      const gap = num(run.summary.capital_gap);
      const stressLast = run.projection.stress[run.projection.stress.length - 1];
      const cet1 = stressLast ? num(stressLast.cet1_ratio_pct) : NaN;

      const carCell = floorIntensity(car, CAR_FLOOR);
      const lcrCell = lcrAssessed ? floorIntensity(lcr, LCR_FLOOR) : { intensity: 0.1, breach: false };
      const erosionIntensity = Math.max(0, Math.min(1, erosion / 8));

      return {
        key: run.run_id,
        label: run.scenario_code,
        sublabel: run.summary.stress_stays_above_all_minima
          ? 'Above all minima'
          : `Breach Y${run.summary.first_breach_year ?? '—'}`,
        badge: (
          <StatusPill tone={run.summary.stress_stays_above_all_minima ? 'success' : 'critical'}>
            {run.summary.stress_stays_above_all_minima ? 'OK' : 'Breach'}
          </StatusPill>
        ),
        active: run.run_id === focusedRunId,
        onClick: () => onFocus(run),
        cells: {
          car: { display: `${car.toFixed(2)}%`, intensity: carCell.intensity, breach: carCell.breach },
          erosion: { display: `${erosion.toFixed(2)}`, intensity: erosionIntensity },
          cet1: { display: Number.isFinite(cet1) ? `${cet1.toFixed(2)}%` : '—', intensity: 0.2 },
          lcr: { display: lcrAssessed ? `${lcr.toFixed(1)}%` : 'n/a', intensity: lcrCell.intensity, breach: lcrCell.breach },
          gap: {
            display: gap.toLocaleString(undefined, { maximumFractionDigits: 0 }),
            intensity: gap > 0 ? 0.6 : 0.1,
            breach: false,
          },
        },
      };
    });

  return (
    <SectionCard
      title="Scenario comparison"
      subtitle={`${runs.length} scenarios · severity ladder (worst CAR erosion first) — click a row to drill into its drivers`}
    >
      <ScenarioHeatmap columns={COLUMNS} rows={rows} />
    </SectionCard>
  );
}
