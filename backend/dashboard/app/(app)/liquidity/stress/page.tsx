'use client';

import { AlertTriangle, Loader2, PlayCircle, Zap } from 'lucide-react';
import type {
  RegulatoryRunRead,
  RegulatoryScenarioCode,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import StressDeltaChart from '@/components/liquidity/charts/StressDeltaChart';
import {
  runComputedAt,
  runMetric,
  runMetricStatus,
  runSectionTotal,
} from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRun,
  useRegulatoryRuns,
  useRunAllLiquidityScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, labelize, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

const STRESS_SCENARIOS = ['idiosyncratic', 'market_wide', 'combined'] as const;

const SCENARIO_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  idiosyncratic: 'Idiosyncratic stress',
  market_wide: 'Market-wide stress',
  combined: 'Combined stress (BoG severe)',
};

const SCENARIO_DESCRIPTIONS: Record<string, string> = {
  idiosyncratic:
    'Counterparty-specific funding shock — stressed deposit run-off rates applied to the reporting period.',
  market_wide:
    'System-wide market stress — inflow haircuts and HQLA securities haircuts applied to the reporting period.',
  combined:
    'Concurrent idiosyncratic and market-wide shock per the BoG ILAAP severe scenario.',
};

export default function StressScenarios() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const runsQuery = useRegulatoryRuns(bankId, {
    module: 'liquidity',
    reportingPeriodId: periodId,
    limit: 100,
  });
  const runAll = useRunAllLiquidityScenarios(bankId);

  // Latest run per scenario — list is created_at descending.
  const latestIds = new Map<string, string>();
  for (const run of runsQuery.data?.runs ?? []) {
    if (!latestIds.has(run.scenarioCode)) {
      latestIds.set(run.scenarioCode, run.id);
    }
  }

  const baselineRun = useRegulatoryRun(bankId, latestIds.get('baseline'));
  const idioRun = useRegulatoryRun(bankId, latestIds.get('idiosyncratic'));
  const marketRun = useRegulatoryRun(bankId, latestIds.get('market_wide'));
  const combinedRun = useRegulatoryRun(bankId, latestIds.get('combined'));

  const runByScenario: Record<string, RegulatoryRunRead | undefined> = {
    baseline: baselineRun.data,
    idiosyncratic: idioRun.data,
    market_wide: marketRun.data,
    combined: combinedRun.data,
  };

  const hasRuns = latestIds.size > 0;
  const baselineLcr = runMetric(baselineRun.data, 'lcr_pct');
  const baselineNsfr = runMetric(baselineRun.data, 'nsfr_pct');
  const baselineHqla = runMetric(baselineRun.data, 'hqla_total_ghs');

  const gridRows = STRESS_SCENARIOS.map((scenario) => {
    const run = runByScenario[scenario];
    const lcr = runMetric(run, 'lcr_pct');
    const nsfr = runMetric(run, 'nsfr_pct');
    return {
      scenario,
      label: SCENARIO_LABELS[scenario] ?? labelize(scenario),
      lcr,
      nsfr,
      lcrDelta: lcr !== null && baselineLcr !== null ? lcr - baselineLcr : null,
      nsfrDelta:
        nsfr !== null && baselineNsfr !== null ? nsfr - baselineNsfr : null,
      status: runMetricStatus(run, 'lcr_pct'),
      run,
    };
  });

  const gridColumns: Column<(typeof gridRows)[number]>[] = [
    {
      key: 'scenario',
      header: 'Scenario',
      width: '30%',
      render: (r) => (
        <div>
          <p className="font-medium text-navy">{r.label}</p>
          <p className="text-caption text-slate">{SCENARIO_DESCRIPTIONS[r.scenario]}</p>
        </div>
      ),
    },
    {
      key: 'lcr',
      header: 'LCR under stress',
      numeric: true,
      render: (r) => (r.lcr === null ? '—' : fmtPct(r.lcr, 2)),
    },
    {
      key: 'lcrDelta',
      header: 'Δ vs baseline',
      numeric: true,
      render: (r) =>
        r.lcrDelta === null ? (
          '—'
        ) : (
          <span className={r.lcrDelta < 0 ? 'text-critical' : 'text-success'}>
            {r.lcrDelta >= 0 ? '+' : ''}
            {r.lcrDelta.toFixed(2)} pts
          </span>
        ),
    },
    {
      key: 'nsfr',
      header: 'NSFR under stress',
      numeric: true,
      render: (r) => (r.nsfr === null ? '—' : fmtPct(r.nsfr, 2)),
    },
    {
      key: 'status',
      header: 'Status',
      align: 'right',
      render: (r) =>
        r.run ? (
          <StatusPill tone={statusTone(r.status)} />
        ) : (
          <StatusPill tone="pending">Not run</StatusPill>
        ),
    },
  ];

  const runAllButton = (
    <button
      type="button"
      disabled={runAll.isPending || !periodId}
      onClick={() => periodId && runAll.mutate({ reportingPeriodId: periodId })}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
    >
      {runAll.isPending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <Zap size={13} aria-hidden />
      )}
      Run all scenarios
    </button>
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'Stress' },
        ]}
        title="Liquidity Stress"
        subtitle="Basel III-aligned liquidity stress per BoG ILAAP framework"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={runAllButton}
      />

      <QueryBoundary
        isLoading={runsQuery.isLoading}
        error={runsQuery.error}
        onRetry={() => runsQuery.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {!hasRuns ? (
            <EmptyState
              Icon={PlayCircle}
              title="No stress runs for this period"
              description={`Run all scenarios to calculate baseline, idiosyncratic, market-wide, and combined liquidity stress results for ${period?.label ?? 'this period'}. Each scenario persists an auditable regulatory run.`}
              action={runAllButton}
            />
          ) : (
            <>
              {/* Baseline reference KPIs */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiStat
                  label="Baseline LCR"
                  value={baselineLcr === null ? '—' : fmtPct(baselineLcr, 2)}
                  hint="Latest stored baseline run"
                />
                <KpiStat
                  label="Baseline NSFR"
                  value={baselineNsfr === null ? '—' : fmtPct(baselineNsfr, 2)}
                />
                <KpiStat
                  label="Baseline HQLA"
                  value={
                    baselineHqla === null ? '—' : fmtCurrency(baselineHqla, 'GHS')
                  }
                />
                <KpiStat label="Survival horizon" value="30" unit="days" />
              </div>

              {/* Scenario grid + delta chart */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <SectionCard
                  className="lg:col-span-2"
                  title="Scenario grid"
                  subtitle="Latest stored run per scenario for this reporting period"
                  noPadding
                  computedAt={runComputedAt(baselineRun.data)}
                  runBadge={
                    baselineRun.data ? <RunBadge run={baselineRun.data} /> : undefined
                  }
                >
                  <DataTable columns={gridColumns} rows={gridRows} />
                </SectionCard>

                <ChartFrame
                  title="Ratio deterioration"
                  subtitle="Stressed ratio minus baseline, per scenario"
                  height={240}
                >
                  <StressDeltaChart
                    data={gridRows.map((r) => ({
                      scenario: labelize(r.scenario),
                      lcrDelta:
                        r.lcrDelta === null
                          ? null
                          : Number(r.lcrDelta.toFixed(2)),
                      nsfrDelta:
                        r.nsfrDelta === null
                          ? null
                          : Number(r.nsfrDelta.toFixed(2)),
                    }))}
                    height={240}
                  />
                </ChartFrame>
              </div>

              {/* Scenario detail cards */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {STRESS_SCENARIOS.map((scenario) => (
                  <ScenarioCard
                    key={scenario}
                    scenario={scenario}
                    run={runByScenario[scenario]}
                    isLoading={latestIds.has(scenario) && !runByScenario[scenario]}
                    baselineLcr={baselineLcr}
                    baselineNsfr={baselineNsfr}
                  />
                ))}
              </div>

              {/* Comparison table */}
              <SectionCard
                title="Scenario comparison"
                subtitle="Latest stored run per scenario for this reporting period"
                noPadding
              >
                <ComparisonTable runs={runByScenario} />
              </SectionCard>
            </>
          )}

          {/* Methodology footer */}
          <SectionCard
            title="Methodology"
            subtitle="ILAAP and Basel III stress alignment"
          >
            <div className="text-body text-navy/85 leading-relaxed space-y-3">
              <p>
                Stress factors applied to baseline LCR and NSFR per BoG&apos;s
                ILAAP framework and Basel III §35-36 (LCR) and §50-52 (NSFR).
                Each scenario reruns the regulatory engines with stressed
                run-off rates, inflow multipliers, and HQLA haircuts, and
                persists an auditable run with its full input snapshot.
              </p>
              <p>
                Idiosyncratic and market-wide shocks are calibrated to BoG
                severe tolerance levels; the combined scenario assumes a
                simultaneous shock with no central bank backstop. ILAAP
                submission quarterly.
              </p>
            </div>
          </SectionCard>
        </div>
      </QueryBoundary>
    </>
  );
}

function ScenarioCard({
  scenario,
  run,
  isLoading,
  baselineLcr,
  baselineNsfr,
}: {
  scenario: RegulatoryScenarioCode;
  run: RegulatoryRunRead | undefined;
  isLoading: boolean;
  baselineLcr: number | null;
  baselineNsfr: number | null;
}) {
  const label = SCENARIO_LABELS[scenario] ?? labelize(scenario);
  const lcr = runMetric(run, 'lcr_pct');
  const nsfr = runMetric(run, 'nsfr_pct');
  const lcrStatus = runMetricStatus(run, 'lcr_pct');
  const breach = lcrStatus === 'red';

  return (
    <SectionCard
      title={label}
      subtitle={SCENARIO_DESCRIPTIONS[scenario]}
      actions={run ? <StatusPill tone={statusTone(lcrStatus)} /> : undefined}
      className={breach ? 'border-l-4 border-l-critical' : ''}
      computedAt={runComputedAt(run)}
      runBadge={run ? <RunBadge run={run} /> : undefined}
    >
      <div className="space-y-5">
        {!run ? (
          <p className="text-body text-slate">
            {isLoading ? 'Loading scenario run…' : 'Not yet run for this period.'}
          </p>
        ) : run.status !== 'succeeded' ? (
          <div className="flex items-start gap-2 px-3 py-2.5 rounded bg-critical-light border border-critical/20">
            <AlertTriangle
              size={14}
              className="text-critical shrink-0 mt-0.5"
              aria-hidden
            />
            <p className="text-caption text-critical leading-relaxed">
              Run {run.status}
              {run.error ? ` — ${run.error.message}` : '.'}
            </p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-2 gap-4">
              <ScenarioMetric
                label="LCR after stress"
                value={lcr}
                baseline={baselineLcr}
                status={lcrStatus}
              />
              <ScenarioMetric
                label="NSFR after stress"
                value={nsfr}
                baseline={baselineNsfr}
                status={runMetricStatus(run, 'nsfr_pct')}
              />
            </div>

            {breach && (
              <div className="flex items-start gap-2 px-3 py-2.5 rounded bg-critical-light border border-critical/20">
                <AlertTriangle
                  size={14}
                  className="text-critical shrink-0 mt-0.5"
                  aria-hidden
                />
                <p className="text-caption text-critical leading-relaxed">
                  LCR falls below the regulatory minimum under this scenario.
                  Activate the contingency funding plan — see the CFP tab.
                </p>
              </div>
            )}

            <ScenarioAssumptions run={run} />
          </>
        )}
      </div>
    </SectionCard>
  );
}

function ScenarioMetric({
  label,
  value,
  baseline,
  status,
}: {
  label: string;
  value: number | null;
  baseline: number | null;
  status: string | null;
}) {
  const change = value !== null && baseline !== null ? value - baseline : null;
  const valueColor =
    status === 'red'
      ? 'text-critical'
      : status === 'amber'
      ? 'text-warning'
      : 'text-success';
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p className="mt-1 font-mono text-h1 tnum">
        <span className={valueColor}>
          {value === null ? '—' : fmtPct(value, 2)}
        </span>
      </p>
      {change !== null && (
        <p
          className={`mt-1 font-mono text-caption tnum ${
            change < 0 ? 'text-critical' : 'text-success'
          }`}
        >
          {change >= 0 ? '+' : ''}
          {change.toFixed(2)} pts vs baseline
        </p>
      )}
    </div>
  );
}

/** Stressed run-off assumptions vs baseline parameters, from the run inputs. */
function ScenarioAssumptions({ run }: { run: RegulatoryRunRead }) {
  const inputs = run.inputs as {
    shocks?: Record<string, string>;
    parameters?: { outflow_runoff_rates_pct?: Record<string, string> };
  };
  const shocks = inputs.shocks ?? {};
  const baseRates = inputs.parameters?.outflow_runoff_rates_pct ?? {};
  const entries = Object.entries(shocks);
  const runoffOverrides = entries.filter(([key]) => key.startsWith('runoff:'));
  const otherShocks = entries.filter(([key]) => !key.startsWith('runoff:'));

  if (!entries.length) {
    return (
      <p className="text-caption text-slate">
        No shocks applied — regulatory base assumptions.
      </p>
    );
  }

  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        Stress assumptions
      </p>
      {runoffOverrides.length > 0 && (
        <table className="mt-2 w-full text-caption border-collapse">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                Run-off category
              </th>
              <th className="text-right py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                Base %
              </th>
              <th className="text-right py-1.5 text-micro font-medium uppercase tracking-wider text-slate">
                Stressed %
              </th>
            </tr>
          </thead>
          <tbody>
            {runoffOverrides.map(([key, value]) => {
              const category = key.slice('runoff:'.length);
              const base = baseRates[category];
              return (
                <tr key={key} className="border-b border-border-light last:border-b-0">
                  <td className="py-1.5 text-navy/90">{labelize(category)}</td>
                  <td className="py-1.5 text-right font-mono text-slate tnum">
                    {base !== undefined ? `${num(base).toFixed(0)}%` : '—'}
                  </td>
                  <td className="py-1.5 text-right font-mono font-medium text-navy tnum">
                    {num(value).toFixed(0)}%
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {otherShocks.length > 0 && (
        <dl className="mt-2 space-y-1">
          {otherShocks.map(([key, value]) => (
            <div key={key} className="flex items-center justify-between gap-3 text-caption">
              <dt className="text-slate">{shockLabel(key)}</dt>
              <dd className="font-mono text-navy tnum">
                {shockValue(key, value)}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function shockLabel(key: string): string {
  if (key === 'inflow_multiplier') return 'Inflow multiplier';
  if (key === 'hqla_securities_haircut_pct') return 'HQLA securities haircut';
  if (key.startsWith('asf:')) return `ASF weight · ${labelize(key.slice(4))}`;
  if (key.startsWith('rsf:')) return `RSF weight · ${labelize(key.slice(4))}`;
  return labelize(key);
}

function shockValue(key: string, value: string): string {
  if (key === 'inflow_multiplier') return `×${num(value)}`;
  if (key.includes('pct') || key.startsWith('asf:') || key.startsWith('rsf:')) {
    return `${num(value).toFixed(0)}%`;
  }
  return value;
}

type ComparisonRow = {
  label: string;
  unit: 'ghs' | 'pct';
  values: (number | null)[];
};

function ComparisonTable({
  runs,
}: {
  runs: Record<string, RegulatoryRunRead | undefined>;
}) {
  const scenarios = ['baseline', ...STRESS_SCENARIOS];
  const rows: ComparisonRow[] = [
    {
      label: 'HQLA',
      unit: 'ghs',
      values: scenarios.map((s) => runMetric(runs[s], 'hqla_total_ghs')),
    },
    {
      label: 'Outflows (weighted)',
      unit: 'ghs',
      values: scenarios.map((s) => runSectionTotal(runs[s], 'outflow')),
    },
    {
      label: 'Inflows (weighted)',
      unit: 'ghs',
      values: scenarios.map((s) => runSectionTotal(runs[s], 'inflow')),
    },
    {
      label: 'Net outflows (30d)',
      unit: 'ghs',
      values: scenarios.map((s) => runMetric(runs[s], 'net_outflows_30d_ghs')),
    },
    {
      label: 'LCR',
      unit: 'pct',
      values: scenarios.map((s) => runMetric(runs[s], 'lcr_pct')),
    },
    {
      label: 'NSFR',
      unit: 'pct',
      values: scenarios.map((s) => runMetric(runs[s], 'nsfr_pct')),
    },
  ];

  const columns: Column<ComparisonRow>[] = [
    {
      key: 'metric',
      header: 'Metric',
      render: (r) => r.label,
      width: '28%',
    },
    ...scenarios.map((scenario, i) => ({
      key: scenario,
      header: SCENARIO_LABELS[scenario] ?? labelize(scenario),
      numeric: true,
      render: (r: ComparisonRow) => {
        const value = r.values[i];
        if (value === null) return '—';
        return r.unit === 'pct' ? fmtPct(value, 2) : fmtCurrency(value, 'GHS');
      },
    })),
  ];

  return <DataTable columns={columns} rows={rows} />;
}
