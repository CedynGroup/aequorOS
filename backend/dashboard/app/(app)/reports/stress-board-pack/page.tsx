'use client';

/**
 * Stress board-pack composer (docs/stress.md §4 item 8). Composes a board-ready
 * pack from a selected immutable enterprise-stress run: cover + provenance,
 * executive summary, the projection charts, driver attribution, the N-way
 * comparison, management-action with/without, analyst/CRO narrative (composer-
 * local capture, item 7), and the full Appendix II Tables 1–6 — printable to PDF
 * via the app's print-optimised board-pack path (design system §5).
 */

import { useMemo, useState } from 'react';
import { Printer } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import KpiStat from '@/components/ui/KpiStat';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import ChartFrame from '@/components/ui/ChartFrame';
import { useBankContext } from '@/components/shell/BankContext';
import { useSdiLiquidityPosition } from '@/components/basel/sdiHooks';
import { num, fmtDateUTC, shortId } from '@/lib/api/values';
import { useEnterpriseStressRegistry, useMacroScenarios } from '@/components/stress/hooks';
import ProjectionPaths from '@/components/stress/charts/ProjectionPaths';
import DriverWaterfall from '@/components/stress/charts/DriverWaterfall';
import ScenarioComparison from '@/components/stress/ScenarioComparison';
import ManagementActionsPanel from '@/components/stress/ManagementActionsPanel';
import AppendixIITables from '@/components/stress/AppendixIITables';

export default function StressBoardPack() {
  const { bank, period, periods, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;
  const isSdi = moduleScope.institutionClass === 'sdi';
  const sdiLiquidity = useSdiLiquidityPosition(isSdi ? bankId : undefined);

  const approved = useMacroScenarios({ status: 'approved' });
  const scenarioIds = useMemo(
    () => (approved.data?.scenarios ?? []).map((s) => s.id),
    [approved.data]
  );
  const registry = useEnterpriseStressRegistry(bankId, periodId, scenarioIds);
  const runs = useMemo(() => registry.data ?? [], [registry.data]);

  const [selectedRunId, setSelectedRunId] = useState('');
  const [analystNote, setAnalystNote] = useState('');
  const [croNote, setCroNote] = useState('');
  const [sections, setSections] = useState({
    charts: true,
    driver: true,
    comparison: true,
    actions: true,
    appendix: true,
  });

  const run = useMemo(
    () => runs.find((r) => r.run_id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/reports' },
          { label: 'Stress board pack' },
        ]}
        title="Stress Board-Pack Composer"
        subtitle={isSdi ? 'Compose a proportionate SDI stress pack from an immutable run — simplified capital, baseline LMTD evidence, commentary, and management actions' : 'Compose a board-ready ICAAP stress pack from an immutable run — Appendix II tables, charts, commentary, management actions'}
        action={
          <button
            type="button"
            className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            disabled={!run}
            onClick={() => window.print()}
          >
            <Printer size={14} /> Print / export PDF
          </button>
        }
      />

      <QueryBoundary isLoading={registry.isLoading || approved.isLoading} error={registry.error ?? approved.error} onRetry={() => registry.refetch()}>
        <div className="px-8 py-6 space-y-6">
          {/* Composer controls (hidden on print) */}
          <div className="print:hidden">
            <SectionCard title="Compose" subtitle="Pick a run and the sections to include">
              <div className="space-y-4">
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <label className="block">
                    <span className="text-caption text-slate">Run</span>
                    <select
                      className="mt-1 w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy"
                      value={run?.run_id ?? ''}
                      onChange={(e) => setSelectedRunId(e.target.value)}
                    >
                      {runs.length === 0 && <option value="">No runs available</option>}
                      {runs.map((r) => (
                        <option key={r.run_id} value={r.run_id}>
                          {r.scenario_code} · {fmtDateUTC(new Date(r.created_at))}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="flex flex-wrap items-end gap-3">
                    {(
                      [
                        ['charts', 'Projection charts'],
                        ['driver', 'Driver attribution'],
                        ['comparison', 'Comparison'],
                        ['actions', 'Management actions'],
                        ['appendix', 'Appendix II'],
                      ] as const
                    ).map(([key, label]) => (
                      <label key={key} className="flex items-center gap-1.5 text-caption text-slate">
                        <input
                          type="checkbox"
                          className="h-4 w-4 accent-action"
                          checked={sections[key]}
                          onChange={(e) => setSections((s) => ({ ...s, [key]: e.target.checked }))}
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <label className="block">
                    <span className="text-caption text-slate">Analyst commentary</span>
                    <textarea className="mt-1 w-full min-h-24 rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy" value={analystNote} onChange={(e) => setAnalystNote(e.target.value)} />
                  </label>
                  <label className="block">
                    <span className="text-caption text-slate">CRO / board challenge</span>
                    <textarea className="mt-1 w-full min-h-24 rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy" value={croNote} onChange={(e) => setCroNote(e.target.value)} />
                  </label>
                </div>
              </div>
            </SectionCard>
          </div>

          {!run ? (
            <EmptyState
              Icon={Printer}
              title="No stress run to compose"
              description="Run an approved macro scenario from the enterprise stress workbench first — the immutable run becomes the source for this board pack."
            />
          ) : (
            <div className="space-y-6">
              {/* Cover */}
              <SectionCard
                title={`${isSdi ? 'SDI Stress Test' : 'ICAAP Stress Test'} — ${run.scenario_code}`}
                subtitle={`${bank?.name ?? ''} · reporting period ${period ? fmtDateUTC(new Date(period.periodEnd)) : ''}`}
                actions={<StatusPill tone={run.summary.stress_stays_above_all_minima ? 'success' : 'critical'}>{run.summary.stress_stays_above_all_minima ? 'Above all minima' : 'Breach'}</StatusPill>}
              >
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                  <KpiStat label="Stressed CAR" value={`${num(run.summary.stressed_car_end_pct).toFixed(2)}%`} status={num(run.summary.stressed_car_end_pct) < num(run.outcome.coupling?.car_min_pct ?? '0') ? 'crit' : 'ok'} hint={`Base ${num(run.summary.baseline_car_end_pct).toFixed(2)}%`} />
                  <KpiStat label="CAR erosion" value={`${num(run.summary.car_erosion_pp).toFixed(2)} pp`} status="warn" />
                  {run.summary.stressed_lcr_pct === null ? (
                    <KpiStat label="Liquidity regime" value="LMTD" status="ok" hint="Basel LCR/NSFR n/a for SDIs (§4.6)" />
                  ) : (
                    <KpiStat label="Stressed LCR" value={`${num(run.summary.stressed_lcr_pct).toFixed(1)}%`} status={num(run.summary.stressed_lcr_pct) < num(run.outcome.coupling?.lcr_min_pct ?? '0') ? 'crit' : 'ok'} />
                  )}
                  <KpiStat label="Capital gap" value={`GHS'000 ${num(run.summary.capital_gap).toLocaleString()}`} status={num(run.summary.capital_gap) > 0 ? 'warn' : 'ok'} />
                </div>
                <p className="mt-4 text-micro text-slate">
                  Immutable run {shortId(run.run_id, 10)} · input hash {shortId(run.input_hash, 12)} · engine {run.engine_version} · reproducible
                </p>
              </SectionCard>

              {(analystNote || croNote) && (
                <SectionCard title="Narrative & assumptions rationale">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                    {analystNote && (
                      <div>
                        <p className="text-caption font-medium uppercase tracking-wider text-slate mb-1">Analyst commentary</p>
                        <p className="text-body text-navy/85 leading-relaxed whitespace-pre-wrap">{analystNote}</p>
                      </div>
                    )}
                    {croNote && (
                      <div>
                        <p className="text-caption font-medium uppercase tracking-wider text-slate mb-1">CRO / board challenge</p>
                        <p className="text-body text-navy/85 leading-relaxed whitespace-pre-wrap">{croNote}</p>
                      </div>
                    )}
                  </div>
                </SectionCard>
              )}

              {sections.charts && (
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <ChartFrame title="CAR — base vs stress" subtitle="vs regulatory floor" height={240}>
                    <ProjectionPaths projection={run.projection} metricKey="car_pct" threshold={num(run.outcome.coupling?.car_min_pct ?? '0')} thresholdLabel="CAR floor" />
                  </ChartFrame>
                  {run.summary.stressed_lcr_pct === null ? (
                    <ChartFrame title="Liquidity — SDI (LMTD)" subtitle="Basel LCR/NSFR excluded (§4.6)" height={240}>
                      <SdiLiquidityBoardDisclosure
                        asOf={sdiLiquidity.data?.as_of}
                        table1Breaches={sdiLiquidity.data?.ratios.filter((ratio) => ratio.status === 'below_minimum').length}
                        reserveBreaches={sdiLiquidity.data?.reserves.filter((reserve) => reserve.status === 'below_minimum').length}
                      />
                    </ChartFrame>
                  ) : (
                    <ChartFrame title="LCR — base vs stress" subtitle="vs regulatory floor" height={240}>
                      <ProjectionPaths projection={run.projection} metricKey="lcr_pct" threshold={num(run.outcome.coupling?.lcr_min_pct ?? '0')} thresholdLabel="LCR floor" />
                    </ChartFrame>
                  )}
                </div>
              )}

              {sections.driver && <DriverWaterfall run={run} />}

              {sections.comparison && runs.length > 1 && (
                <ScenarioComparison runs={runs} focusedRunId={run.run_id} onFocus={(r) => setSelectedRunId(r.run_id)} />
              )}

              {sections.actions && <ManagementActionsPanel run={run} />}

              {sections.appendix && <AppendixIITables tables={run.appendix_ii} flat />}
            </div>
          )}
        </div>
      </QueryBoundary>
    </>
  );
}

function SdiLiquidityBoardDisclosure({
  asOf,
  table1Breaches,
  reserveBreaches,
}: {
  asOf: string | undefined;
  table1Breaches: number | undefined;
  reserveBreaches: number | undefined;
}) {
  return (
    <div className="flex h-full flex-col items-center justify-center px-6 text-center text-caption text-slate">
      <p>
        SDI liquidity stress is not assessed because no BoG SDI liquidity-stress methodology is configured.
        Basel LCR/NSFR are not substituted.
      </p>
      {asOf && (
        <p className="mt-3 text-navy">
          Baseline LMTD evidence as of {asOf}: {table1Breaches ?? 0} Table 1 breach(es), {reserveBreaches ?? 0} reserve breach(es).
        </p>
      )}
    </div>
  );
}
