'use client';

import { AlertTriangle, CheckCircle2, Loader2, PlayCircle, Zap } from 'lucide-react';
import type { RegulatoryRunRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import CapitalProjectionChart from '@/components/charts/CapitalProjectionChart';
import { runComputedAt } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useRegulatoryRun,
  useRegulatoryRuns,
  useRunAllCapitalScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtPct, regShort } from '@/lib/format';

const STRESS_SCENARIOS = ['mild', 'moderate', 'severe'] as const;

const SCENARIO_LABELS: Record<string, string> = {
  baseline: 'Baseline',
  mild: 'Mild — Baseline path',
  moderate: 'Moderate — Slowdown',
  severe: 'Severe — supervisory ICAAP severe',
};

const SCENARIO_DESCRIPTIONS: Record<string, string> = {
  mild: 'Modest RWA growth with retained earnings accruing — the strategic plan path over four quarters.',
  moderate:
    'Growth slowdown with elevated credit losses and RWA drift over the four-quarter horizon.',
  severe:
    'Supervisory ICAAP severe scenario — RWA inflation, income compression, and credit-loss spike applied concurrently.',
};

/** Raw shapes carried in RegulatoryRunRead.metrics (snake_case dicts). */
type StressPathRow = {
  quarter: number;
  cet1_capital: string;
  tier1_capital: string;
  total_capital: string;
  credit_rwa: string;
  market_rwa: string;
  operational_rwa: string;
  total_rwa: string;
  cet1_ratio: string;
  tier1_ratio: string;
  car: string;
  leverage_ratio: string;
};

type StressTrigger = {
  code: string;
  threshold_pct: string;
  fired: boolean;
  first_quarter: number | null;
  action: string;
};

function stressPath(run: RegulatoryRunRead | undefined): StressPathRow[] {
  const path = run?.metrics?.['stress_path'];
  return Array.isArray(path) ? (path as StressPathRow[]) : [];
}

function stressTriggers(run: RegulatoryRunRead | undefined): StressTrigger[] {
  const triggers = run?.metrics?.['triggers'];
  return Array.isArray(triggers) ? (triggers as StressTrigger[]) : [];
}

function runMetric(run: RegulatoryRunRead | undefined, key: string): number | null {
  const value = run?.metrics?.[key];
  if (value === undefined || value === null) return null;
  return num(value as string);
}

function endState(run: RegulatoryRunRead | undefined): StressPathRow | null {
  const path = stressPath(run);
  return path.length ? path[path.length - 1] : null;
}

/** Scenario severity from the trigger states of the stored run. */
function scenarioTone(run: RegulatoryRunRead | undefined): StatusTone {
  const triggers = stressTriggers(run);
  if (!triggers.length) return 'pending';
  if (triggers.some((t) => (t.code === 'breach' || t.code === 'critical') && t.fired)) {
    return 'critical';
  }
  if (triggers.some((t) => t.fired)) return 'amber';
  return 'success';
}

const TRIGGER_LABELS: Record<string, string> = {
  early_warning: 'Early warning',
  breach: 'Regulatory breach',
  critical: 'Critical floor',
};

export default function CapitalStress() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const runsQuery = useRegulatoryRuns(bankId, {
    module: 'capital',
    reportingPeriodId: periodId,
    limit: 100,
  });
  const runAll = useRunAllCapitalScenarios(bankId);

  // Latest run per scenario — list is created_at descending.
  const latestIds = new Map<string, string>();
  for (const run of runsQuery.data?.runs ?? []) {
    if (!latestIds.has(run.scenarioCode)) {
      latestIds.set(run.scenarioCode, run.id);
    }
  }

  const baselineRun = useRegulatoryRun(bankId, latestIds.get('baseline'));
  const mildRun = useRegulatoryRun(bankId, latestIds.get('mild'));
  const moderateRun = useRegulatoryRun(bankId, latestIds.get('moderate'));
  const severeRun = useRegulatoryRun(bankId, latestIds.get('severe'));

  const runByScenario: Record<string, RegulatoryRunRead | undefined> = {
    baseline: baselineRun.data,
    mild: mildRun.data,
    moderate: moderateRun.data,
    severe: severeRun.data,
  };

  const hasStressRuns = STRESS_SCENARIOS.some((s) => latestIds.has(s));
  const carMin = num(
    baselineRun.data?.metricResults.find((m) => m.metricCode === 'car_pct')
      ?.thresholdMin ?? '10'
  );

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
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Stress Testing' },
        ]}
        title="Capital Stress Testing"
        subtitle={`ICAAP-style stress · Four-quarter CAR projection · ${regShort()} severe scenario`}
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={runAllButton}
      />

      <QueryBoundary
        isLoading={runsQuery.isLoading}
        error={runsQuery.error}
        onRetry={() => runsQuery.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {!hasStressRuns ? (
            <EmptyState
              Icon={PlayCircle}
              title="No capital stress runs for this period"
              description={`Run all scenarios to calculate baseline, mild, moderate, and severe capital stress results for ${period?.label ?? 'this period'}. Each scenario persists an auditable regulatory run.`}
              action={runAllButton}
            />
          ) : (
            <>
              {/* Baseline reference strip — as-of ratios */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <BaselineCell
                  label="Baseline CAR"
                  value={runMetric(baselineRun.data, 'car_pct')}
                />
                <BaselineCell
                  label="Baseline Tier 1"
                  value={runMetric(baselineRun.data, 'tier1_ratio_pct')}
                />
                <BaselineCell
                  label="Baseline CET1"
                  value={runMetric(baselineRun.data, 'cet1_ratio_pct')}
                />
                <BaselineCell
                  label="Baseline leverage"
                  value={runMetric(baselineRun.data, 'leverage_ratio_pct')}
                />
              </div>

              {/* Scenario cards */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                {STRESS_SCENARIOS.map((scenario) => (
                  <ScenarioCard
                    key={scenario}
                    scenario={scenario}
                    run={runByScenario[scenario]}
                    isLoading={
                      latestIds.has(scenario) && !runByScenario[scenario]
                    }
                    baselineCar={runMetric(baselineRun.data, 'car_pct')}
                    carMin={carMin}
                  />
                ))}
              </div>

              {/* Q4 comparison */}
              <SectionCard
                title="Quarter 4 comparison"
                subtitle="End-state position across scenarios · Latest stored run per scenario"
                noPadding
                computedAt={runComputedAt(baselineRun.data)}
                runBadge={
                  baselineRun.data ? <RunBadge run={baselineRun.data} /> : undefined
                }
              >
                <ComparisonTable runs={runByScenario} carMin={carMin} />
              </SectionCard>
            </>
          )}

          {/* Methodology footer */}
          <SectionCard
            title="Methodology"
            subtitle={`ICAAP and ${regShort()} CRD stress alignment`}
          >
            <div className="text-body text-navy/85 leading-relaxed space-y-3">
              <p>
                Each scenario projects the four-quarter capital path from the
                as-of position: CET1 evolves by retained quarterly income net
                of credit losses while RWAs grow at the stressed quarterly
                rate. Ratios are re-derived each quarter and evaluated against
                the {regShort()} early-warning (10.5%), minimum (10%), and critical
                (9%) CAR thresholds.
              </p>
              <p>
                Trigger actions follow the pre-defined Recovery &amp;
                Resolution Plan escalation. Every scenario persists an
                auditable regulatory run with its full input snapshot and
                engine version.
              </p>
            </div>
          </SectionCard>
        </div>
      </QueryBoundary>
    </>
  );
}

function BaselineCell({ label, value }: { label: string; value: number | null }) {
  return (
    <KpiStat
      label={label}
      value={value === null ? '—' : fmtPct(value, 2)}
      hint="Latest stored baseline run"
    />
  );
}

function ScenarioCard({
  scenario,
  run,
  isLoading,
  baselineCar,
  carMin,
}: {
  scenario: (typeof STRESS_SCENARIOS)[number];
  run: RegulatoryRunRead | undefined;
  isLoading: boolean;
  baselineCar: number | null;
  carMin: number;
}) {
  const label = SCENARIO_LABELS[scenario] ?? labelize(scenario);
  const end = endState(run);
  const endCar = end ? num(end.car) : null;
  const endTier1 = end ? num(end.tier1_ratio) : null;
  const tone = scenarioTone(run);
  const breach = endCar !== null && endCar < carMin;
  const triggers = stressTriggers(run);

  const chartData = stressPath(run).map((row) => ({
    month: `Q${row.quarter}`,
    car: num(row.car),
    tier1: num(row.tier1_ratio),
  }));

  return (
    <SectionCard
      title={label}
      subtitle={SCENARIO_DESCRIPTIONS[scenario]}
      actions={run ? <StatusPill tone={tone} /> : undefined}
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
            <div className="grid grid-cols-2 gap-5">
              <div>
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  End-state CAR (Q4)
                </p>
                <p
                  className={`mt-1 font-mono text-h1 tabular-nums ${
                    endCar !== null && endCar >= carMin
                      ? 'text-success'
                      : 'text-critical'
                  }`}
                >
                  {endCar === null ? '—' : fmtPct(endCar, 2)}
                </p>
                {baselineCar !== null && endCar !== null && (
                  <p className="text-caption text-slate">
                    from {fmtPct(baselineCar, 2)}
                  </p>
                )}
              </div>
              <div>
                <p className="text-micro font-medium uppercase tracking-wider text-slate">
                  End-state Tier 1 (Q4)
                </p>
                <p className="mt-1 font-mono text-h1 text-navy tabular-nums">
                  {endTier1 === null ? '—' : fmtPct(endTier1, 2)}
                </p>
                {end && (
                  <p className="text-caption text-slate">
                    RWA {fmtCurrency(num(end.total_rwa))}
                  </p>
                )}
              </div>
            </div>

            {chartData.length > 0 && (
              <CapitalProjectionChart
                data={chartData}
                bogMin={carMin}
                internalBuffer={10.5}
                bufferLabel="Early warning"
                criticalFloor={9}
                height={260}
              />
            )}

            {/* Trigger panel */}
            <div>
              <p className="text-micro font-medium uppercase tracking-wider text-slate mb-2">
                Capital action triggers
              </p>
              <ul className="space-y-2">
                {triggers.map((trigger) => (
                  <li
                    key={trigger.code}
                    className={`flex items-start gap-2 px-3 py-2.5 rounded border ${
                      trigger.fired
                        ? 'bg-critical-light border-critical/20'
                        : 'bg-surface border-border-light'
                    }`}
                  >
                    {trigger.fired ? (
                      <AlertTriangle
                        size={14}
                        className="text-critical shrink-0 mt-0.5"
                        aria-hidden
                      />
                    ) : (
                      <CheckCircle2
                        size={14}
                        className="text-success shrink-0 mt-0.5"
                        aria-hidden
                      />
                    )}
                    <div className="min-w-0 flex-1">
                      <p
                        className={`text-caption font-medium ${
                          trigger.fired ? 'text-critical' : 'text-navy'
                        }`}
                      >
                        {TRIGGER_LABELS[trigger.code] ?? labelize(trigger.code)}{' '}
                        · CAR &lt; {num(trigger.threshold_pct).toFixed(1)}%
                        {trigger.fired && trigger.first_quarter !== null
                          ? ` · fired Q${trigger.first_quarter}`
                          : ' · not fired'}
                      </p>
                      <p
                        className={`mt-0.5 text-caption leading-relaxed ${
                          trigger.fired ? 'text-critical/90' : 'text-slate'
                        }`}
                      >
                        {trigger.action}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

          </>
        )}
      </div>
    </SectionCard>
  );
}

type ComparisonRow = {
  label: string;
  unit: 'ghs' | 'pct';
  values: (number | null)[];
};

function ComparisonTable({
  runs,
  carMin,
}: {
  runs: Record<string, RegulatoryRunRead | undefined>;
  carMin: number;
}) {
  const scenarios = ['baseline', ...STRESS_SCENARIOS];

  const q4 = (scenario: string, pick: (row: StressPathRow) => string): number | null => {
    const run = runs[scenario];
    // Baseline runs have no stress path — use the as-of metrics instead.
    const end = endState(run);
    if (end) return num(pick(end));
    return null;
  };

  const asOf = (scenario: string, key: string): number | null =>
    runMetric(runs[scenario], key);

  const rows: ComparisonRow[] = [
    {
      label: 'CAR',
      unit: 'pct',
      values: scenarios.map(
        (s) => q4(s, (r) => r.car) ?? asOf(s, 'car_pct')
      ),
    },
    {
      label: 'Tier 1 ratio',
      unit: 'pct',
      values: scenarios.map(
        (s) => q4(s, (r) => r.tier1_ratio) ?? asOf(s, 'tier1_ratio_pct')
      ),
    },
    {
      label: 'CET1 ratio',
      unit: 'pct',
      values: scenarios.map(
        (s) => q4(s, (r) => r.cet1_ratio) ?? asOf(s, 'cet1_ratio_pct')
      ),
    },
    {
      label: 'Leverage ratio',
      unit: 'pct',
      values: scenarios.map(
        (s) => q4(s, (r) => r.leverage_ratio) ?? asOf(s, 'leverage_ratio_pct')
      ),
    },
    {
      label: 'Total capital',
      unit: 'ghs',
      values: scenarios.map(
        (s) => q4(s, (r) => r.total_capital) ?? asOf(s, 'total_capital_ghs')
      ),
    },
    {
      label: 'Total RWA',
      unit: 'ghs',
      values: scenarios.map(
        (s) => q4(s, (r) => r.total_rwa) ?? asOf(s, 'total_rwa_ghs')
      ),
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
      header:
        scenario === 'baseline'
          ? 'Baseline (as-of)'
          : SCENARIO_LABELS[scenario] ?? labelize(scenario),
      numeric: true,
      render: (r: ComparisonRow) => {
        const value = r.values[i];
        if (value === null) return '—';
        if (r.unit === 'ghs') return fmtCurrency(value);
        const breach = r.label === 'CAR' && value < carMin;
        return (
          <span className={breach ? 'text-critical font-medium' : undefined}>
            {fmtPct(value, 2)}
          </span>
        );
      },
    })),
  ];

  return <DataTable columns={columns} rows={rows} />;
}
