'use client';

/**
 * Enterprise stress workbench (docs/stress.md §4) — the real workbench that
 * replaces the per-module shock table. One approved macro scenario drives every
 * engine into a 3-year enterprise projection (Appendix II), which the surface
 * LEADS with as charts: ratio-vs-threshold paths, base-vs-stress projections and
 * a driver-attribution waterfall. Governed scenario library + builder,
 * designated base case, re-openable run registry, N-way comparison + drill-down,
 * management-action with/without, and a link to the board-pack composer.
 *
 * The `moduleLens` prop only labels the focus a given route arrives from; the
 * run itself is bank-wide (enterprise), coupling solvency and liquidity.
 */

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';
import { Activity, FileBarChart, Play } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import SubTabs from '@/components/ui/SubTabs';
import KpiStat from '@/components/ui/KpiStat';
import ChartFrame from '@/components/ui/ChartFrame';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import { ApiError } from '@/lib/api/client';
import { num } from '@/lib/api/values';
import { useBankContext } from '@/components/shell/BankContext';
import { type SdiLiquidityPosition, useSdiCapitalSummary, useSdiLiquidityPosition } from '@/components/basel/sdiHooks';
import ScenarioLibrary from './ScenarioLibrary';
import ScenarioBuilder from './ScenarioBuilder';
import ProjectionPaths from './charts/ProjectionPaths';
import DriverWaterfall from './charts/DriverWaterfall';
import ScenarioComparison from './ScenarioComparison';
import AppendixIITables from './AppendixIITables';
import ManagementActionsPanel from './ManagementActionsPanel';
import SignoffPanel from './SignoffPanel';
import RunRegistry from './RunRegistry';
import {
  useMacroScenario,
  useMacroScenarios,
  useManagementActionPlans,
  useRunEnterpriseStress,
  useEnterpriseStressRegistry,
} from './hooks';
import type { EnterpriseStressRead, MacroScenarioSummary } from './types';

export type StressModuleLens = 'capital' | 'liquidity' | 'irr' | 'fx' | 'ftp';

const LENS_LABEL: Record<StressModuleLens, string> = {
  capital: 'Capital lens',
  liquidity: 'Liquidity lens',
  irr: 'IRRBB lens',
  fx: 'FX lens',
  ftp: 'FTP lens',
};

type Tab = 'run' | 'results' | 'compare' | 'appendix' | 'actions' | 'governance' | 'registry';

const TABS: { key: Tab; label: string }[] = [
  { key: 'results', label: 'Results' },
  { key: 'compare', label: 'Comparison' },
  { key: 'appendix', label: 'Appendix II' },
  { key: 'actions', label: 'Management actions' },
  { key: 'governance', label: 'Governance & sign-off' },
  { key: 'registry', label: 'Run registry' },
  { key: 'run', label: 'Scenarios & run' },
];

export default function EnterpriseStressWorkbench({ moduleLens }: { moduleLens?: StressModuleLens }) {
  const { bank, period, periods, moduleScope, isLoading } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;
  const isSdiTenant = moduleScope.institutionClass === 'sdi';

  const [tab, setTab] = useState<Tab>('results');
  const [builderOpen, setBuilderOpen] = useState(false);
  const [editScenarioId, setEditScenarioId] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [focusedRun, setFocusedRun] = useState<EnterpriseStressRead | null>(null);

  // Run configuration
  const [runScenarioId, setRunScenarioId] = useState('');
  const [horizon, setHorizon] = useState(3);
  const [carTarget, setCarTarget] = useState('13');
  const [planId, setPlanId] = useState('');
  const [includeIrr, setIncludeIrr] = useState(true);
  const [includeFx, setIncludeFx] = useState(true);
  const [reason, setReason] = useState('');
  const [runError, setRunError] = useState<string | null>(null);

  const approved = useMacroScenarios({ status: 'approved' });
  const approvedScenarios = useMemo<MacroScenarioSummary[]>(
    () => approved.data?.scenarios ?? [],
    [approved.data]
  );
  const plans = useManagementActionPlans(bankId);
  const approvedPlans = useMemo(
    () => (plans.data?.plans ?? []).filter((p) => p.status === 'approved'),
    [plans.data]
  );
  const editScenario = useMacroScenario(editScenarioId ?? undefined);
  const registry = useEnterpriseStressRegistry(
    bankId,
    periodId,
    approvedScenarios.map((s) => s.id)
  );
  const runMutation = useRunEnterpriseStress(bankId);
  const sdiCapital = useSdiCapitalSummary(isSdiTenant ? bankId : undefined);
  const sdiLiquidity = useSdiLiquidityPosition(isSdiTenant ? bankId : undefined);

  useEffect(() => {
    if (isSdiTenant) {
      setCarTarget('10');
      setIncludeFx(false);
    }
  }, [isSdiTenant]);

  // Default the focused run to the worst run in the registry when none picked.
  const effectiveRun = useMemo(() => {
    if (focusedRun) return focusedRun;
    const runs = registry.data ?? [];
    if (runs.length === 0) return null;
    return [...runs].sort((a, b) => num(b.summary.car_erosion_pp) - num(a.summary.car_erosion_pp))[0];
  }, [focusedRun, registry.data]);

  const toggleSelect = (s: MacroScenarioSummary) => {
    setSelectedIds((ids) => (ids.includes(s.id) ? ids.filter((i) => i !== s.id) : [...ids, s.id]));
  };

  const openBuilder = (scenarioId?: string) => {
    setEditScenarioId(scenarioId ?? null);
    setBuilderOpen(true);
    setTab('run');
  };

  const runStress = async () => {
    setRunError(null);
    if (!bankId || !periodId || !runScenarioId || !reason.trim()) {
      setRunError('Select an approved scenario and enter a run reason.');
      return;
    }
    try {
      const result = await runMutation.mutateAsync({
        scenario_id: runScenarioId,
        reporting_period_id: periodId,
        horizon_years: horizon,
        car_target_pct: carTarget,
        management_action_plan_id: planId || null,
        include_irr: includeIrr,
        include_fx: includeFx,
        reason: reason.trim(),
      });
      setFocusedRun(result);
      setTab('results');
    } catch (e) {
      setRunError(
        e instanceof ApiError ? `${e.errorCode ?? e.code ?? 'error'}: ${e.message}` : 'The stress run failed.'
      );
    }
  };

  if (isLoading) {
    return (
      <div className="px-1 py-4">
        <EmptyState
          Icon={Activity}
          title="Loading institution context"
          description="Loading the active institution and its reporting periods."
        />
      </div>
    );
  }

  if (!bankId) {
    return (
      <div className="px-1 py-4">
        <EmptyState
          Icon={Activity}
          title="Institution context unavailable"
          description="The stress workbench needs an active institution before it can load its scenario library and run registry."
          action={
            <Link href="/data-engine" className="btn-primary px-4 py-2 text-body font-medium">
              Open Data Engine
            </Link>
          }
        />
      </div>
    );
  }

  if (!periodId) {
    return (
      <div className="px-1 py-4">
        <EmptyState
          Icon={Activity}
          title="A reporting period is required"
          description="No derived reporting period is available for this institution. Activate a canonical book in the Data Engine to create the period and enable governed stress runs."
          action={
            <Link href="/data-engine" className="btn-primary px-4 py-2 text-body font-medium">
              Open Data Engine
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header strip */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-2">
          {moduleLens && <StatusPill tone="action">{LENS_LABEL[moduleLens]}</StatusPill>}
          <span className="text-caption text-slate">
            {isSdiTenant
              ? 'Enterprise solvency and proportionate risk stress for a specialised deposit-taking institution'
              : 'Enterprise-wide stress · one macro scenario → all engines → Appendix II (¶40, ¶50, ¶68)'}
          </span>
        </div>
        <Link
          href="/reports/stress-board-pack"
          className="inline-flex items-center gap-1.5 rounded-md border border-border-light px-3 py-1.5 text-caption font-medium text-navy hover:bg-surface"
        >
          <FileBarChart size={14} /> Board-pack composer
        </Link>
      </div>

      <SubTabs items={TABS} active={tab} onChange={(k) => setTab(k as Tab)} />

      {tab === 'run' && (
        <div className="grid grid-cols-1 xl:grid-cols-5 gap-6 items-start">
          <div className="xl:col-span-2 space-y-6">
            <ScenarioLibrary
              selectedIds={selectedIds}
              onToggleSelect={toggleSelect}
              onEdit={(id) => openBuilder(id)}
              onNew={() => openBuilder()}
            />
          </div>
          <div className="xl:col-span-3 space-y-6">
            {builderOpen ? (
              <ScenarioBuilder
                defaultBankId={bankId}
                editScenario={editScenarioId ? editScenario.data ?? null : null}
                onSaved={() => {
                  setBuilderOpen(false);
                  setEditScenarioId(null);
                  void approved.refetch();
                }}
                onCancel={() => {
                  setBuilderOpen(false);
                  setEditScenarioId(null);
                }}
              />
            ) : (
              <SectionCard
                title="Run enterprise stress"
                subtitle={isSdiTenant ? 'Run an approved scenario against the simplified Section 29 capital regime and material SDI risks' : 'Drive an approved scenario through every engine into the immutable 3-year projection'}
              >
                <div className="space-y-4">
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <label className="block">
                      <span className="text-caption text-slate">Approved scenario</span>
                      <select
                        className={selectCls}
                        value={runScenarioId}
                        onChange={(e) => setRunScenarioId(e.target.value)}
                      >
                        <option value="">Select…</option>
                        {approvedScenarios.map((s) => (
                          <option key={s.id} value={s.id}>
                            {s.name} ({s.severity ?? s.scenario_type})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-caption text-slate">Management-action plan (optional)</span>
                      <select className={selectCls} value={planId} onChange={(e) => setPlanId(e.target.value)}>
                        <option value="">None (pre-action only)</option>
                        {approvedPlans.map((p) => (
                          <option key={p.id} value={p.id}>{p.name}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block">
                      <span className="text-caption text-slate">Horizon (years, ≥3)</span>
                      <input
                        type="number"
                        min={3}
                        max={10}
                        className={selectCls}
                        value={horizon}
                        onChange={(e) => setHorizon(Math.max(3, Math.min(10, Number(e.target.value) || 3)))}
                      />
                    </label>
                    <label className="block">
                      <span className="text-caption text-slate">Capital adequacy target (%)</span>
                      <input className={selectCls} value={carTarget} onChange={(e) => setCarTarget(e.target.value)} />
                    </label>
                  </div>
                  <div className="flex flex-wrap gap-4">
                    <label className="flex items-center gap-2 text-caption text-slate">
                      <input type="checkbox" className="h-4 w-4 accent-action" checked={includeIrr} onChange={(e) => setIncludeIrr(e.target.checked)} />
                      Include IRRBB
                    </label>
                    {!isSdiTenant && (
                      <label className="flex items-center gap-2 text-caption text-slate">
                        <input type="checkbox" className="h-4 w-4 accent-action" checked={includeFx} onChange={(e) => setIncludeFx(e.target.checked)} />
                        Include FX
                      </label>
                    )}
                  </div>
                  <label className="block">
                    <span className="text-caption text-slate">Run reason (required — governance)</span>
                    <input className={selectCls} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={isSdiTenant ? 'Quarterly proportionate stress' : 'Quarterly ICAAP stress'} />
                  </label>
                  {runError && <p className="text-caption text-critical">{runError}</p>}
                  <button
                    type="button"
                    className="btn-primary inline-flex items-center gap-1.5 px-4 py-2 text-body font-medium disabled:opacity-50"
                    disabled={runMutation.isPending}
                    onClick={() => void runStress()}
                  >
                    <Play size={15} /> {runMutation.isPending ? 'Running…' : 'Run enterprise stress'}
                  </button>
                </div>
              </SectionCard>
            )}
          </div>
        </div>
      )}

      {tab === 'results' && (
        <ResultsView
          run={effectiveRun}
          onConfigure={() => setTab('run')}
          sdiCapitalFloor={sdiCapital.data?.car_min_pct}
          sdiLiquidity={sdiLiquidity.data}
        />
      )}

      {tab === 'compare' && (
        <ScenarioComparison
          runs={registry.data ?? []}
          focusedRunId={effectiveRun?.run_id}
          onFocus={(run) => {
            setFocusedRun(run);
            setTab('results');
          }}
        />
      )}

      {tab === 'appendix' &&
        (effectiveRun ? (
          <AppendixIITables tables={effectiveRun.appendix_ii} />
        ) : (
          <NoRun onConfigure={() => setTab('run')} />
        ))}

      {tab === 'actions' &&
        (effectiveRun ? (
          <ManagementActionsPanel run={effectiveRun} />
        ) : (
          <NoRun onConfigure={() => setTab('run')} />
        ))}

      {tab === 'governance' && <SignoffPanel run={effectiveRun} bankId={bankId} />}

      {tab === 'registry' && (
        <RunRegistry
          bankId={bankId}
          periodId={periodId}
          approvedScenarios={approvedScenarios}
          periods={periods}
          activeRunId={effectiveRun?.run_id}
          onOpen={(run) => {
            setFocusedRun(run);
            setTab('results');
          }}
        />
      )}
    </div>
  );
}

function ResultsView({
  run,
  onConfigure,
  sdiCapitalFloor,
  sdiLiquidity,
}: {
  run: EnterpriseStressRead | null;
  onConfigure: () => void;
  sdiCapitalFloor: string | undefined;
  sdiLiquidity: SdiLiquidityPosition | undefined;
}) {
  if (!run) return <NoRun onConfigure={onConfigure} />;
  const s = run.summary;
  const coupling = run.outcome.coupling;
  // An SDI run omits the Basel liquidity leg (docs/sdi.md §4.6): no coupling block,
  // null LCR in the summary. Present the s.29 solvency view without a Basel LCR
  // verdict — never render LCR against a floor that does not apply to an SDI.
  const isSdi = !coupling || s.stressed_lcr_pct === null;
  const carFloor = num(coupling?.car_min_pct ?? sdiCapitalFloor ?? '0');
  const lcrFloor = num(coupling?.lcr_min_pct ?? '0');

  return (
    <div className="space-y-6">
      {/* KPI band */}
      <div className="grid grid-cols-2 md:grid-cols-3 xl:grid-cols-6 gap-3">
        <KpiStat
          label="Stressed CAR"
          value={`${num(s.stressed_car_end_pct).toFixed(2)}%`}
          status={num(s.stressed_car_end_pct) < carFloor ? 'crit' : 'ok'}
          hint={`Base ${num(s.baseline_car_end_pct).toFixed(2)}%${carFloor > 0 ? ` · floor ${carFloor.toFixed(0)}%` : ''}`}
        />
        <KpiStat label="CAR erosion" value={`${num(s.car_erosion_pp).toFixed(2)} pp`} status="warn" hint="Base → stress, final year" />
        {isSdi ? (
          <KpiStat
            label="Liquidity regime"
            value="LMTD"
            status="ok"
            hint="Basel LCR/NSFR not assessed for SDIs (§4.6)"
          />
        ) : (
          <KpiStat
            label="Stressed LCR"
            value={`${num(s.stressed_lcr_pct).toFixed(1)}%`}
            status={num(s.stressed_lcr_pct) < lcrFloor ? 'crit' : 'ok'}
            hint={`Base ${num(s.baseline_lcr_pct).toFixed(1)}% · floor ${lcrFloor.toFixed(0)}%`}
          />
        )}
        <KpiStat
          label="Stays above minima"
          value={s.stress_stays_above_all_minima ? 'Yes' : `Breach Y${s.first_breach_year ?? '—'}`}
          status={s.stress_stays_above_all_minima ? 'ok' : 'crit'}
          hint={s.binding_minima.length ? `Binding: ${s.binding_minima.join(', ')}` : 'All minima held'}
        />
        <KpiStat label="Capital gap" value={`GHS'000 ${num(s.capital_gap).toLocaleString()}`} status={num(s.capital_gap) > 0 ? 'warn' : 'ok'} hint="Worst-year, pre-action" />
        {isSdi ? (
          <KpiStat label="Capital regime" value="s.29" status="ok" hint="Simplified SDI capital adequacy" />
        ) : (
          <KpiStat
            label="Solvency × liquidity"
            value={coupling!.both_breached ? 'Both breach' : coupling!.car_breached || coupling!.lcr_breached ? 'One breach' : 'Both hold'}
            status={coupling!.both_breached ? 'crit' : coupling!.car_breached || coupling!.lcr_breached ? 'warn' : 'ok'}
            hint="¶59(f) interlinkage"
          />
        )}
      </div>

      {/* Charts lead */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <ChartFrame title="CAR — base vs stress" subtitle={isSdi ? 'Section 29 capital adequacy path' : `Capital adequacy path vs the ${carFloor.toFixed(0)}% floor`} height={240}>
          <ProjectionPaths projection={run.projection} metricKey="car_pct" threshold={carFloor > 0 ? carFloor : undefined} thresholdLabel={carFloor > 0 ? `CAR floor ${carFloor.toFixed(0)}%` : undefined} />
        </ChartFrame>
        {isSdi ? (
          <ChartFrame title="Liquidity — SDI (LMTD)" subtitle="Basel LCR/NSFR excluded for an SDI (§4.6)" height={240}>
            <SdiLiquidityNotAssessed position={sdiLiquidity} />
          </ChartFrame>
        ) : (
          <ChartFrame title="LCR — base vs stress" subtitle={`Liquidity coverage path vs the ${lcrFloor.toFixed(0)}% floor`} height={240}>
            <ProjectionPaths projection={run.projection} metricKey="lcr_pct" threshold={lcrFloor} thresholdLabel={`LCR floor ${lcrFloor.toFixed(0)}%`} />
          </ChartFrame>
        )}
        {!isSdi && (
          <>
            <ChartFrame title="CET1 — base vs stress" subtitle="Common equity Tier 1 ratio path" height={240}>
              <ProjectionPaths projection={run.projection} metricKey="cet1_ratio_pct" />
            </ChartFrame>
            <ChartFrame title="Tier 1 & leverage" subtitle="Tier 1 ratio path over the horizon" height={240}>
              <ProjectionPaths projection={run.projection} metricKey="tier1_ratio_pct" />
            </ChartFrame>
          </>
        )}
      </div>

      <DriverWaterfall run={run} />

      {isSdi ? (
        <SectionCard title="Liquidity regime" subtitle="SDI liquidity stress (docs/sdi.md §4.6)">
          <SdiLiquidityNotAssessed position={sdiLiquidity} />
        </SectionCard>
      ) : (
        <SectionCard title="Solvency–liquidity narrative" subtitle="The directive's interlinkage read (¶59(f))">
          <p className="text-body text-navy/85 leading-relaxed">{coupling!.narrative}</p>
        </SectionCard>
      )}
    </div>
  );
}

function SdiLiquidityNotAssessed({ position }: { position: SdiLiquidityPosition | undefined }) {
  const breached = position?.ratios.filter((ratio) => ratio.status === 'below_minimum').length ?? 0;
  const reserveBreaches = position?.reserves.filter((reserve) => reserve.status === 'below_minimum').length ?? 0;
  return (
    <div className="flex h-full flex-col justify-center px-6 text-center text-caption text-slate">
      <p>
        Liquidity stress is not assessed because no BoG SDI liquidity-stress methodology is configured.
        Basel LCR/NSFR and a synthetic survival-horizon result are intentionally not substituted.
      </p>
      {position && (
        <p className="mt-3 text-navy">
          Baseline LMTD evidence as of {position.as_of}: {breached} Table 1 breach(es) and {reserveBreaches} reserve breach(es).
        </p>
      )}
    </div>
  );
}

function NoRun({ onConfigure }: { onConfigure?: () => void }) {
  return (
    <EmptyState
      Icon={Activity}
      title="No stress run selected"
      description="Run an approved scenario from the Scenarios & run tab, or re-open one from the run registry, to see its projection charts, driver attribution and the Appendix II tables."
      action={
        onConfigure ? (
          <button type="button" className="btn-primary px-4 py-2 text-body font-medium" onClick={onConfigure}>
            Go to Scenarios &amp; run
          </button>
        ) : undefined
      }
    />
  );
}

const selectCls =
  'mt-1 w-full rounded-md border border-border-light bg-transparent px-3 py-2 text-body text-navy';
