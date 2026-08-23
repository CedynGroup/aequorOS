'use client';

/**
 * N-way scenario comparison (docs/stress.md §4 item 6) — a scenario × metric
 * matrix, not a two-run A/B. Leads with the severity-ladder heatmap (rows sorted
 * worst-CAR-erosion first) so the board reads the whole selected scenario set at
 * a glance; clicking a row focuses that run for driver drill-down.
 *
 * FAIL CLOSED. Every threshold in this matrix comes from the run payload — the
 * run's own coupling block for a bank, the SDI capital summary for an SDI. This
 * file used to carry `CAR_FLOOR = 13` / `LCR_FLOOR = 100` as module constants,
 * which painted an SDI's compliant 11% CAR (s.29 floor 10%) as a breach and
 * captioned the column "floor 13%". When a run carries no floor the cell is
 * shown WITHOUT a verdict — never shaded green, never shaded as a breach.
 */

import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ScenarioHeatmap, { type HeatCell, type HeatColumn, type HeatRow } from './charts/ScenarioHeatmap';
import { assessAgainstFloor, fmtFloorPct, num, numOrNull } from '@/lib/api/values';
import { currencyCode } from '@/lib/format';
import type { EnterpriseStressRead } from './types';

/**
 * Map a floor-type ratio to a 0..1 intensity (0 safe … 1 at/below floor), or to
 * an explicitly unshaded cell when the comparison cannot be made.
 */
function floorCell(
  value: number | null,
  floor: number | null,
  display: string,
  notAssessedNote: string
): HeatCell {
  const assessment = assessAgainstFloor(value, floor);
  if (!assessment.assessed) {
    // The heatmap shades intensity < 0.33 on the GREEN ramp, so an unassessed
    // cell must never sit below it. 0.33 is the first attention-band step: the
    // cell reads as unresolved, and the title says why.
    return { display, intensity: 0.33, title: notAssessedNote };
  }
  const headroom = assessment.value - assessment.floor;
  const band = assessment.floor * 0.5;
  const intensity = Math.max(0, Math.min(1, 1 - headroom / band));
  return {
    display,
    intensity,
    breach: assessment.breach,
    title: `Floor ${fmtFloorPct(assessment.floor)}`,
  };
}

/**
 * The column caption for a floor-type metric: the shared floor when every run
 * in the matrix carries the same one, "per run" when they differ, and an
 * explicit "no floor configured" when none is available.
 */
function floorCaption(floors: (number | null)[]): string {
  const known = floors.filter((f): f is number => f !== null);
  if (known.length === 0) return 'no floor configured';
  const distinct = Array.from(new Set(known));
  if (known.length < floors.length) {
    return distinct.length === 1
      ? `floor ${fmtFloorPct(distinct[0])} where configured`
      : 'floor varies by run';
  }
  return distinct.length === 1 ? `floor ${fmtFloorPct(distinct[0])}` : 'floor varies by run';
}

export default function ScenarioComparison({
  runs,
  focusedRunId,
  isSdiTenant = false,
  sdiCapitalFloor,
  onFocus,
}: {
  runs: EnterpriseStressRead[];
  focusedRunId?: string;
  /**
   * The tenant's institution class — the authority on which regime applies.
   * Defaults to false so a caller that does not know stays on the bank framing
   * rather than silently reframing a bank run as an SDI one.
   */
  isSdiTenant?: boolean;
  /** The SDI s.29 capital floor from the control plane, when the tenant is an SDI. */
  sdiCapitalFloor?: string | null;
  onFocus: (run: EnterpriseStressRead) => void;
}) {
  if (runs.length === 0) return null;

  const sdiFloor = numOrNull(sdiCapitalFloor);

  const ordered = [...runs].sort(
    (a, b) => num(b.summary.car_erosion_pp) - num(a.summary.car_erosion_pp)
  );

  const perRun = ordered.map((run) => {
    const coupling = run.outcome.coupling;
    // Basel LCR and the ¶59(f) coupling are absent from an SDI run
    // (docs/sdi.md §4.6); they are also absent from a bank run whose liquidity
    // leg produced nothing. Shape decides presence, tenant class decides meaning.
    const liquidityAssessed = Boolean(coupling) && run.summary.stressed_lcr_pct !== null;
    const stressLast = run.projection.stress[run.projection.stress.length - 1];
    return {
      run,
      car: numOrNull(run.summary.stressed_car_end_pct),
      // Bank: the run's own coupling minimum. SDI: the s.29 floor from the
      // control plane. Neither present ⇒ null ⇒ no verdict.
      carFloor: numOrNull(coupling?.car_min_pct) ?? (isSdiTenant ? sdiFloor : null),
      erosion: numOrNull(run.summary.car_erosion_pp),
      // CET1 is a Basel three-tier concept: an SDI's s.29 capital build has no
      // CET1 line and the projection carries null. Gate it exactly as LCR is
      // gated — `num(null) → 0` used to render "Stressed CET1 0.00%".
      cet1: isSdiTenant ? null : numOrNull(stressLast?.cet1_ratio_pct),
      liquidityAssessed,
      lcr: liquidityAssessed ? numOrNull(run.summary.stressed_lcr_pct) : null,
      lcrFloor: numOrNull(coupling?.lcr_min_pct),
      gap: num(run.summary.capital_gap),
    };
  });

  const columns: HeatColumn[] = [
    { key: 'car', label: 'Stressed CAR', sub: floorCaption(perRun.map((r) => r.carFloor)) },
    { key: 'erosion', label: 'CAR erosion', sub: 'pp' },
    {
      key: 'cet1',
      label: 'Stressed CET1',
      sub: isSdiTenant ? 'not applicable (s.29)' : '',
    },
    {
      key: 'lcr',
      label: 'Stressed LCR',
      sub: isSdiTenant
        ? 'not assessed (LMTD)'
        : floorCaption(perRun.filter((r) => r.liquidityAssessed).map((r) => r.lcrFloor)),
    },
    { key: 'gap', label: 'Capital gap', sub: `${currencyCode()}'000` },
  ];

  const rows: HeatRow[] = perRun.map((r) => ({
    key: r.run.run_id,
    label: r.run.scenario_code,
    sublabel: r.run.summary.stress_stays_above_all_minima
      ? 'Above all minima'
      : `Breach Y${r.run.summary.first_breach_year ?? '—'}`,
    badge: (
      <StatusPill tone={r.run.summary.stress_stays_above_all_minima ? 'success' : 'critical'}>
        {r.run.summary.stress_stays_above_all_minima ? 'OK' : 'Breach'}
      </StatusPill>
    ),
    active: r.run.run_id === focusedRunId,
    onClick: () => onFocus(r.run),
    cells: {
      car: floorCell(
        r.car,
        r.carFloor,
        r.car === null ? 'not computed' : `${r.car.toFixed(2)}%`,
        r.car === null
          ? 'Stressed CAR was not computed for this run — no compliance verdict.'
          : 'No capital floor is configured for this run — no compliance verdict.'
      ),
      erosion: {
        display: r.erosion === null ? '—' : r.erosion.toFixed(2),
        intensity: r.erosion === null ? 0.33 : Math.max(0, Math.min(1, r.erosion / 8)),
      },
      cet1: {
        display:
          r.cet1 === null ? (isSdiTenant ? 'n/a' : 'not computed') : `${r.cet1.toFixed(2)}%`,
        intensity: r.cet1 === null ? 0.33 : 0.2,
        title: r.cet1 === null
          ? isSdiTenant
            ? 'CET1 is a Basel tier concept; the s.29 SDI capital build has no CET1 line.'
            : 'The final stress year carries no CET1 ratio — not computed.'
          : undefined,
      },
      lcr: r.liquidityAssessed
        ? floorCell(
            r.lcr,
            r.lcrFloor,
            r.lcr === null ? 'not computed' : `${r.lcr.toFixed(1)}%`,
            'No LCR floor is configured for this run — no compliance verdict.'
          )
        : {
            display: isSdiTenant ? 'n/a' : 'not assessed',
            intensity: 0.33,
            title: isSdiTenant
              ? 'Basel LCR is not assessed for an SDI (docs/sdi.md §4.6).'
              : 'This run carries no Basel liquidity leg — the coverage ratio was not measured.',
          },
      gap: {
        display: r.gap.toLocaleString(undefined, { maximumFractionDigits: 0 }),
        intensity: r.gap > 0 ? 0.6 : 0.1,
        breach: false,
      },
    },
  }));

  return (
    <SectionCard
      title="Scenario comparison"
      subtitle={`${runs.length} scenarios · severity ladder (worst CAR erosion first) — click a row to drill into its drivers. Thresholds are read from each run; a cell with no configured floor carries no compliance verdict.`}
    >
      <ScenarioHeatmap columns={columns} rows={rows} />
    </SectionCard>
  );
}
