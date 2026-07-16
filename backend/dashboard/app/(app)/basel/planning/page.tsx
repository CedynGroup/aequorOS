'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, TrendingUp } from 'lucide-react';
import type { ForecastRunSummaryRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import SubTabs from '@/components/ui/SubTabs';
import RunBadge from '@/components/ui/RunBadge';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import CapitalPlanChart from '@/components/basel/charts/CapitalPlanChart';
import IllustrativeBadge from '@/components/liquidity/IllustrativeBadge';
import { runComputedAt } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCapitalDashboard,
  useForecastRun,
  useForecastRuns,
} from '@/lib/api/hooks';
import { fmtDateUTC, labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

const SCENARIO_ORDER = ['base', 'adverse', 'severely_adverse', 'custom'] as const;

const SCENARIO_LABELS: Record<string, string> = {
  base: 'Base',
  adverse: 'Adverse',
  severely_adverse: 'Severely adverse',
  custom: 'Custom',
};

type PathRow = {
  label: string;
  car: number;
  tier1: number;
  cet1: number;
  netIncome: number;
  totalAssets: number;
};

const pathColumns: Column<PathRow>[] = [
  { key: 'year', header: 'Projection year', render: (r) => r.label, width: '24%' },
  {
    key: 'car',
    header: 'CAR',
    numeric: true,
    render: (r) => fmtPct(r.car, 2),
  },
  {
    key: 'tier1',
    header: 'Tier 1',
    numeric: true,
    render: (r) => fmtPct(r.tier1, 2),
  },
  {
    key: 'cet1',
    header: 'CET1',
    numeric: true,
    render: (r) => fmtPct(r.cet1, 2),
  },
  {
    key: 'ni',
    header: 'Net income',
    numeric: true,
    render: (r) => fmtCurrency(r.netIncome, 'GHS'),
  },
  {
    key: 'assets',
    header: 'Total assets',
    numeric: true,
    render: (r) => fmtCurrency(r.totalAssets, 'GHS'),
  },
];

export default function CapitalPlanning() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useCapitalDashboard(bankId, periodId);
  const forecastRuns = useForecastRuns(bankId, { limit: 100 });

  // Latest succeeded forecast run per scenario for the selected period.
  const latestByScenario = useMemo(() => {
    const map = new Map<string, ForecastRunSummaryRead>();
    const runs = (forecastRuns.data?.runs ?? [])
      .filter((r) => r.reportingPeriodId === periodId && r.status === 'succeeded')
      .sort((a, b) => b.createdAt.getTime() - a.createdAt.getTime());
    for (const run of runs) {
      if (!map.has(run.scenarioCode)) map.set(run.scenarioCode, run);
    }
    return map;
  }, [forecastRuns.data, periodId]);

  const availableScenarios = SCENARIO_ORDER.filter((s) =>
    latestByScenario.has(s)
  );
  const [scenarioChoice, setScenarioChoice] = useState<string | null>(null);
  const activeScenario =
    scenarioChoice && latestByScenario.has(scenarioChoice)
      ? scenarioChoice
      : availableScenarios[0] ?? null;

  const activeRunId = activeScenario
    ? latestByScenario.get(activeScenario)?.id
    : undefined;
  const forecastRun = useForecastRun(bankId, activeRunId);

  const data = dashboard.data;
  const carMin = num(data?.buffers.carMinPct ?? '10');
  const carEarlyWarning = num(data?.buffers.carEarlyWarningPct ?? '10.5');

  // ----- Real base position (latest stored capital figures) -----
  const totalRwa = num(data?.metrics.totalRwaGhs);
  const cet1 = num(data?.capitalStructure.cet1CapitalGhs);
  const at1 = num(data?.capitalStructure.at1CapitalGhs);
  const tier2 = num(data?.capitalStructure.tier2CapitalGhs);
  const totalCapital = num(data?.capitalStructure.totalCapitalGhs);
  const currentCar = num(data?.metrics.carPct);

  // ----- What-if planner state (client-side illustration) -----
  const [rwaGrowthPct, setRwaGrowthPct] = useState(0);
  const [retainedPct, setRetainedPct] = useState(0); // % of current total capital
  const [at1IssuePct, setAt1IssuePct] = useState(0);
  const [tier2IssuePct, setTier2IssuePct] = useState(0);

  const retained = (retainedPct / 100) * totalCapital;
  const at1New = (at1IssuePct / 100) * totalCapital;
  const tier2New = (tier2IssuePct / 100) * totalCapital;

  const proRwa = totalRwa * (1 + rwaGrowthPct / 100);
  const proCet1 = cet1 + retained;
  const proTier1 = proCet1 + at1 + at1New;
  const proTotal = proTier1 + tier2 + tier2New;
  const proCar = proRwa > 0 ? (proTotal / proRwa) * 100 : 0;
  const proCet1Ratio = proRwa > 0 ? (proCet1 / proRwa) * 100 : 0;
  const proTier1Ratio = proRwa > 0 ? (proTier1 / proRwa) * 100 : 0;

  const path = forecastRun.data?.path ?? [];
  const chartData = path.map((y) => ({
    label: y.periodLabel,
    car: num(y.carPct),
    tier1: num(y.tier1RatioPct),
    cet1: num(y.cet1RatioPct),
  }));
  const pathRows: PathRow[] = path.map((y) => ({
    label: y.periodLabel,
    car: num(y.carPct),
    tier1: num(y.tier1RatioPct),
    cet1: num(y.cet1RatioPct),
    netIncome: num(y.netIncome),
    totalAssets: num(y.totalAssets),
  }));
  const summary = forecastRun.data?.summary;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'Planning' },
        ]}
        title="Capital Planning"
        subtitle="Multi-year capital ratio projection from stored forecast runs · what-if planner on the current base"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading || forecastRuns.isLoading}
        error={dashboard.error ?? forecastRuns.error}
        onRetry={() => {
          void dashboard.refetch();
          void forecastRuns.refetch();
        }}
      >
        {data && (
        <div className="px-8 py-6 space-y-6">
          {/* ------- Forecast-driven projection (real stored runs) ------- */}
          {availableScenarios.length === 0 ? (
            <EmptyState
              Icon={TrendingUp}
              title="No forecast runs for this period"
              description="Capital ratio projections come from stored balance-sheet forecast runs. Run a scenario in the Forecasting module to see the five-year CAR, Tier 1, and CET1 path here."
              action={
                <Link
                  href="/forecasting"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  Open Forecasting
                  <ArrowRight size={13} aria-hidden />
                </Link>
              }
            />
          ) : (
            <>
              <SubTabs
                items={availableScenarios.map((s) => ({
                  key: s,
                  label: `${SCENARIO_LABELS[s] ?? labelize(s)} scenario`,
                }))}
                active={activeScenario ?? ''}
                onChange={setScenarioChoice}
              />

              {summary && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                  <KpiStat
                    label="Year-5 CAR"
                    value={fmtPct(num(summary.year5CarPct), 2)}
                    status={num(summary.year5CarPct) >= carMin ? 'ok' : 'crit'}
                    hint={`BoG minimum ${carMin.toFixed(1)}%`}
                  />
                  <KpiStat
                    label="Minimum CAR on path"
                    value={fmtPct(num(summary.minCarPct), 2)}
                    status={num(summary.minCarPct) >= carMin ? 'ok' : 'crit'}
                    hint="Worst projected year"
                  />
                  <KpiStat
                    label="Average ROE"
                    value={fmtPct(num(summary.avgRoePct), 2)}
                    hint="Across the projection"
                  />
                  <KpiStat
                    label="Cumulative net income"
                    value={fmtCurrency(num(summary.cumulativeNetIncome), 'GHS')}
                    hint="Retained earnings feed CET1"
                  />
                </div>
              )}

              <ChartFrame
                title={`Capital ratio projection — ${
                  SCENARIO_LABELS[activeScenario ?? ''] ?? labelize(activeScenario ?? '')
                }`}
                subtitle="Five-year CAR / Tier 1 / CET1 path from the stored forecast run"
                height={300}
                loading={forecastRun.isLoading}
                actions={
                  forecastRun.data ? <RunBadge run={forecastRun.data} /> : undefined
                }
                footer={
                  forecastRun.data ? (
                    <span>
                      Forecast run {forecastRun.data.scenarioCode} · engine{' '}
                      {forecastRun.data.engineVersion} · created{' '}
                      {fmtDateUTC(forecastRun.data.createdAt)}
                    </span>
                  ) : undefined
                }
              >
                {chartData.length > 0 ? (
                  <CapitalPlanChart
                    data={chartData}
                    carMin={carMin}
                    earlyWarning={carEarlyWarning}
                    earlyWarningLabel={
                      data?.buffers.carEarlyWarningLabel || 'Early warning'
                    }
                    height={300}
                  />
                ) : (
                  <div className="h-full flex items-center justify-center text-body text-slate">
                    {forecastRun.isLoading
                      ? 'Loading projection…'
                      : 'No projection path on this run.'}
                  </div>
                )}
              </ChartFrame>

              {pathRows.length > 0 && (
                <SectionCard
                  title="Projection detail"
                  subtitle="Per-year ratios and drivers from the forecast engine"
                  noPadding
                  computedAt={runComputedAt(forecastRun.data)}
                  runBadge={
                    forecastRun.data ? <RunBadge run={forecastRun.data} /> : undefined
                  }
                >
                  <DataTable columns={pathColumns} rows={pathRows} />
                </SectionCard>
              )}
            </>
          )}

          {/* ------- What-if planner (client-side, illustrative) ------- */}
          <SectionCard
            title="What-if capital planner"
            subtitle="Pro-forma ratios recomputed client-side from the latest stored RWA and capital base — not a regulatory calculation"
            actions={<IllustrativeBadge label="What-if · client-side" />}
            footer={
              <span>
                Base position: RWA {fmtCurrency(totalRwa, 'GHS')} · total
                capital {fmtCurrency(totalCapital, 'GHS')} · CAR{' '}
                {fmtPct(currentCar, 2)} (stored values). Sliders apply simple
                arithmetic on these figures; run a forecast scenario for a
                governed projection.
              </span>
            }
          >
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
              {/* Sliders */}
              <div className="space-y-5">
                <PlannerSlider
                  label="RWA growth"
                  value={rwaGrowthPct}
                  onChange={setRwaGrowthPct}
                  min={-20}
                  max={40}
                  step={1}
                  display={`${rwaGrowthPct >= 0 ? '+' : ''}${rwaGrowthPct}%`}
                  hint={`Pro-forma RWA ${fmtCurrency(proRwa, 'GHS')}`}
                />
                <PlannerSlider
                  label="Retained earnings added to CET1"
                  value={retainedPct}
                  onChange={setRetainedPct}
                  min={0}
                  max={25}
                  step={0.5}
                  display={`${retainedPct.toFixed(1)}% of capital`}
                  hint={fmtCurrency(retained, 'GHS')}
                />
                <PlannerSlider
                  label="New AT1 issuance"
                  value={at1IssuePct}
                  onChange={setAt1IssuePct}
                  min={0}
                  max={15}
                  step={0.5}
                  display={`${at1IssuePct.toFixed(1)}% of capital`}
                  hint={fmtCurrency(at1New, 'GHS')}
                />
                <PlannerSlider
                  label="New Tier 2 issuance"
                  value={tier2IssuePct}
                  onChange={setTier2IssuePct}
                  min={0}
                  max={15}
                  step={0.5}
                  display={`${tier2IssuePct.toFixed(1)}% of capital`}
                  hint={fmtCurrency(tier2New, 'GHS')}
                />
              </div>

              {/* Pro-forma outcome */}
              <div className="space-y-5">
                <div className="grid grid-cols-3 gap-4">
                  <KpiStat
                    label="Pro-forma CAR"
                    value={proCar.toFixed(2)}
                    unit="%"
                    delta={proCar - currentCar}
                    status={
                      proCar < carMin
                        ? 'crit'
                        : proCar < carEarlyWarning
                        ? 'warn'
                        : 'ok'
                    }
                    hint={`Now ${fmtPct(currentCar, 2)}`}
                  />
                  <KpiStat
                    label="Pro-forma Tier 1"
                    value={proTier1Ratio.toFixed(2)}
                    unit="%"
                    hint={`Now ${fmtPct(num(data?.metrics.tier1RatioPct), 2)}`}
                  />
                  <KpiStat
                    label="Pro-forma CET1"
                    value={proCet1Ratio.toFixed(2)}
                    unit="%"
                    hint={`Now ${fmtPct(num(data?.metrics.cet1RatioPct), 2)}`}
                  />
                </div>
                <LimitBar
                  label="Pro-forma CAR vs BoG floors"
                  value={proCar}
                  limit={carMin}
                  warnAt={carEarlyWarning}
                  direction="above"
                  unit="%"
                  limitLabel="BoG minimum"
                  warnLabel={data?.buffers.carEarlyWarningLabel || 'Early warning'}
                  format={(v) => v.toFixed(1)}
                />
                <p className="text-caption text-slate leading-relaxed">
                  Pro-forma capital = CET1 {fmtCurrency(proCet1, 'GHS')} + AT1{' '}
                  {fmtCurrency(at1 + at1New, 'GHS')} + Tier 2{' '}
                  {fmtCurrency(tier2 + tier2New, 'GHS')} ={' '}
                  <span className="font-mono text-navy">
                    {fmtCurrency(proTotal, 'GHS')}
                  </span>{' '}
                  over RWA{' '}
                  <span className="font-mono text-navy">
                    {fmtCurrency(proRwa, 'GHS')}
                  </span>
                  .
                </p>
              </div>
            </div>
          </SectionCard>
        </div>
        )}
      </QueryBoundary>
    </>
  );
}

function PlannerSlider({
  label,
  value,
  onChange,
  min,
  max,
  step,
  display,
  hint,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  min: number;
  max: number;
  step: number;
  display: string;
  hint?: string;
}) {
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3 mb-1.5">
        <label className="text-caption font-medium text-navy">{label}</label>
        <span className="font-mono text-caption font-semibold text-navy tnum">
          {display}
        </span>
      </div>
      <input
        type="range"
        value={value}
        min={min}
        max={max}
        step={step}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-[rgb(var(--accent))]"
        aria-label={label}
      />
      {hint && <p className="mt-1 text-caption text-slate">{hint}</p>}
    </div>
  );
}
