'use client';

/**
 * Balance Sheet Forecasting — projection workspace for the persisted forecast
 * run (user-selectable 3–10 year horizon, 5-year regulatory default):
 * assets/liabilities/equity chart with base↔adverse band, per-period delta
 * decomposition waterfall, horizon table, and regulatory ratio paths. All
 * figures come off the saved projection payload.
 */

import { Suspense, useMemo, useState } from 'react';
import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { ArrowUpRight, Loader2, PlayCircle } from 'lucide-react';
import type {
  ForecastRunRead,
  ForecastScenarioCode,
  ProjectionYearRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import Sparkline from '@/components/ui/Sparkline';
import StatusPill from '@/components/ui/StatusPill';
import EmptyState from '@/components/ui/EmptyState';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import DeltaBadge from '@/components/ui/DeltaBadge';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { SkeletonChart } from '@/components/ui/Skeleton';
import BalanceSheetProjectionChart from '@/components/charts/BalanceSheetProjectionChart';
import ProjectionChart, {
  type ProjectionPoint,
} from '@/components/forecasting/charts/ProjectionChart';
import WaterfallChart, {
  type WaterfallStep,
} from '@/components/forecasting/charts/WaterfallChart';
import RatioPathChart from '@/components/forecasting/charts/RatioPathChart';
import { useScenarioRunSet } from '@/components/forecasting/hooks';
import {
  liabilitiesOf,
  metricStatus,
  metricThreshold,
  scenarioLabel,
  yearLabel,
  yoyPct,
} from '@/components/forecasting/lib';
import { useBankContext } from '@/components/shell/BankContext';
import SdiModuleContext from '@/components/sdi/SdiModuleContext';
import {
  useCreateForecastRun,
  useForecastRun,
  useForecastRuns,
  useLiveSummary,
} from '@/lib/api/hooks';
import { isoDate, labelize, num, statusTone } from '@/lib/api/values';
import { currencyCode, fmtCurrency, fmtPct, fmtPctSigned, regShort } from '@/lib/format';
import { seriesColor } from '@/lib/chartTheme';

const PRESET_SCENARIOS: { code: ForecastScenarioCode; label: string }[] = [
  { code: 'base', label: 'Base case' },
  { code: 'adverse', label: 'Adverse' },
  { code: 'severely_adverse', label: 'Severely adverse' },
];

/** Selectable projection horizons; 5 years is the regulatory default. */
const HORIZON_YEARS_OPTIONS = [3, 5, 7, 10] as const;
const DEFAULT_HORIZON_YEARS = 5;

/** A run's actual horizon is the last projected year on its stored path. */
function runHorizonYears(run: ForecastRunRead): number {
  return run.path.reduce(
    (max, point) => Math.max(max, point.year),
    0
  );
}

function kpiTone(status: string | null): 'ok' | 'warn' | 'crit' | undefined {
  switch (status) {
    case 'green':
      return 'ok';
    case 'amber':
      return 'warn';
    case 'red':
      return 'crit';
    default:
      return undefined;
  }
}

export default function BalanceSheetForecastPage() {
  return (
    <Suspense
      fallback={
        <div className="px-8 py-6">
          <SkeletonChart height={320} />
        </div>
      }
    >
      <BalanceSheetWorkspace />
    </Suspense>
  );
}

function BalanceSheetWorkspace() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;
  const searchParams = useSearchParams();
  const requestedRunId = searchParams.get('run');

  const [scenario, setScenario] = useState<ForecastScenarioCode>('base');
  const [horizonYears, setHorizonYears] = useState<number>(DEFAULT_HORIZON_YEARS);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const runsQuery = useForecastRuns(bankId, { limit: 50 });
  const runs = runsQuery.data?.runs ?? [];
  const liveSummary = useLiveSummary(bankId);
  const liveForecast = liveSummary.data?.modules.find((module) => module.module === 'forecast');
  const activeRunId =
    selectedRunId ?? requestedRunId ?? null;

  const runQuery = useForecastRun(bankId, activeRunId);
  const createRun = useCreateForecastRun(bankId);
  const scenarioSet = useScenarioRunSet(bankId);

  const run = runQuery.data;

  // Adverse band: latest succeeded adverse run on the same reporting period,
  // only overlaid when viewing a non-adverse run.
  const adverse =
    run &&
    scenarioSet.adverse &&
    scenarioSet.adverse.id !== run.id &&
    run.scenarioCode !== 'adverse' &&
    scenarioSet.adverse.reportingPeriodId === run.reportingPeriodId
      ? scenarioSet.adverse
      : undefined;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Balance Sheet Forecasting' },
          { label: 'Balance Sheet' },
        ]}
        title="Balance Sheet Forecast"
        subtitle={`Deterministic ${horizonYears}-year projection from canonical financials and persisted scenario assumptions`}
        action={
          <div className="flex items-center gap-2">
            <select
              value={scenario}
              onChange={(e) =>
                setScenario(e.target.value as ForecastScenarioCode)
              }
              aria-label="Forecast scenario"
              className="px-3 py-2 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
            >
              {PRESET_SCENARIOS.map((s) => (
                <option key={s.code} value={s.code}>
                  {s.label}
                </option>
              ))}
            </select>
            <select
              value={horizonYears}
              onChange={(e) => setHorizonYears(Number(e.target.value))}
              aria-label="Forecast horizon in years"
              className="px-3 py-2 text-caption font-medium text-navy border border-border rounded-md bg-surface-raised hover:bg-surface"
            >
              {HORIZON_YEARS_OPTIONS.map((years) => (
                <option key={years} value={years}>
                  {years} years
                  {years === DEFAULT_HORIZON_YEARS ? ' (default)' : ''}
                </option>
              ))}
            </select>
            <button
              type="button"
              disabled={createRun.isPending || !periodId}
              title={periodId ? undefined : 'A derived reporting period is required before a forecast can be run.'}
              onClick={() =>
                periodId &&
                createRun.mutate(
                  {
                    reportingPeriodId: periodId,
                    scenarioCode: scenario,
                    horizonYears,
                  },
                  { onSuccess: (created) => setSelectedRunId(created.id) }
                )
              }
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {createRun.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <PlayCircle size={13} aria-hidden />
              )}
              Run forecast
            </button>
          </div>
        }
      />

      {!periodId && (
        <div className="px-8 pt-6">
          <div className="border-l-4 border-l-warning bg-warning-light/40 px-5 py-4 text-body text-navy">
            <p className="font-medium">A reporting period is required to run a forecast</p>
            <p className="mt-1 text-slate">
              This institution has no derived reporting period yet. Activate the current canonical book in the{' '}
              <Link href="/data-engine" className="text-action hover:underline">
                Data Engine
              </Link>{' '}
              to create the immutable period snapshot used by forecast runs.
            </p>
          </div>
        </div>
      )}

      <SdiModuleContext title="SDI ALM context">
        This projection supports deposit, loan, liquidity, and capital planning for a specialised deposit-taking institution. It does not imply a Basel ICAAP requirement.
      </SdiModuleContext>

      <QueryBoundary
        isLoading={runsQuery.isLoading}
        error={runsQuery.error}
        onRetry={() => runsQuery.refetch()}
      >
        <div className="px-8 py-6 space-y-6">
          {createRun.error && (
            <ErrorPanel error={createRun.error} title="Forecast run failed" />
          )}

          {!activeRunId ? (
            liveForecast && liveForecast.status !== 'na' ? (
              <LiveForecastBaseline forecast={liveForecast} />
            ) : (
              <EmptyState
                Icon={PlayCircle}
                title="Live forecast is not available yet"
                description="Ingest current financial data and reviewed base assumptions. The live pipeline will calculate the baseline automatically; saved and official forecasts remain optional evidence snapshots."
              />
            )
          ) : runQuery.isLoading ? (
            <SkeletonChart height={320} />
          ) : runQuery.error ? (
            <ErrorPanel error={runQuery.error} onRetry={() => runQuery.refetch()} />
          ) : run && run.status !== 'succeeded' ? (
            <ErrorPanel
              error={
                new Error(
                  run.error?.message ??
                    `Run ${labelize(run.status)} — no projection output available.`
                )
              }
              title={`Run ${labelize(run.status)}`}
            />
          ) : run ? (
            <RunDashboard run={run} adverse={adverse} />
          ) : null}
        </div>
      </QueryBoundary>
    </>
  );
}

function LiveForecastBaseline({
  forecast,
}: {
  forecast: { metrics: Record<string, unknown>; computedAt: Date; status: string };
}) {
  const metrics = forecast.metrics;
  const metric = (key: string) => metrics[key] as string | number | undefined;
  return (
    <>
      <SectionCard
        title="Current live forecast baseline"
        subtitle="Five-year projection from current canonical financials and active base assumptions. This is not a saved or official run."
        computedAt={forecast.computedAt}
      >
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
          <KpiStat label="Year-5 CAR" value={fmtPct(num(metric('year5_car_pct')), 2)} />
          <KpiStat label="Year-5 LCR" value={fmtPct(num(metric('year5_lcr_pct')), 2)} />
          <KpiStat label="Year-5 NSFR" value={fmtPct(num(metric('year5_nsfr_pct')), 2)} />
          <KpiStat label="Average ROE" value={fmtPct(num(metric('avg_roe_pct')), 2)} />
        </div>
      </SectionCard>
      <p className="text-caption text-slate">
        Select a saved run through a direct run link, or create a scenario above to preserve an explicit projection snapshot.
      </p>
    </>
  );
}

// ---------------------------------------------------------------------------
// Run dashboard sections
// ---------------------------------------------------------------------------

function RunDashboard({
  run,
  adverse,
}: {
  run: ForecastRunRead;
  adverse: ForecastRunRead | undefined;
}) {
  const path = run.path;
  // The run's actual horizon: desk runs may project 3–10 years, not only 5.
  const horizon = runHorizonYears(run);
  const y0 = path.find((p) => p.year === 0);
  const y1 = path.find((p) => p.year === 1);
  const yFinal = path.find((p) => p.year === horizon);

  const assetPath = path.map((p) => num(p.totalAssets));
  const equityPath = path.map((p) => num(p.equity));

  // Year-1 (12-month) projected asset growth, derived from the stored path.
  const y1AssetGrowth =
    y0 && y1 ? yoyPct(num(y1.totalAssets), num(y0.totalAssets)) : null;
  const horizonAssetGrowth =
    y0 && yFinal ? yoyPct(num(yFinal.totalAssets), num(y0.totalAssets)) : null;

  const carThreshold = metricThreshold(run, 'year5_car_pct', 10);
  const lcrThreshold = metricThreshold(run, 'year5_lcr_pct', 100);
  const nsfrThreshold = metricThreshold(run, 'year5_nsfr_pct', 100);

  const projectionData: ProjectionPoint[] = path.map((p) => {
    const adversePoint = adverse?.path.find((a) => a.year === p.year);
    const baseAssets = num(p.totalAssets);
    const adverseAssets = adversePoint ? num(adversePoint.totalAssets) : null;
    return {
      label: yearLabel(p),
      assets: baseAssets,
      liabilities: liabilitiesOf(p),
      equity: num(p.equity),
      adverseAssets,
      band:
        adverseAssets === null
          ? null
          : [Math.min(baseAssets, adverseAssets), Math.max(baseAssets, adverseAssets)],
    };
  });

  const compositionData = path.map((p) => ({
    month: yearLabel(p),
    loans: Math.round(num(p.loans) / 1_000_000),
    securities: Math.round(num(p.securities) / 1_000_000),
    cash: Math.round(num(p.cash) / 1_000_000),
  }));

  const computedAt = run.createdAt;

  return (
    <div className="space-y-6">
      {/* Scenario context strip */}
      <div className="flex items-center gap-3 flex-wrap">
        <StatusPill tone="action">{scenarioLabel(run.scenarioCode)} scenario</StatusPill>
        <StatusPill tone={horizon === DEFAULT_HORIZON_YEARS ? 'compliant' : 'action'}>
          {horizon}-year horizon
        </StatusPill>
        {adverse && (
          <span className="text-caption text-slate">
            Adverse band overlaid from run{' '}
            <span className="font-mono text-navy">{adverse.id.slice(0, 8)}</span>{' '}
            on the same period
          </span>
        )}
        <span className="ml-auto">
          <Link
            href="/forecasting/scenario"
            className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
          >
            Manage runs in Scenarios <ArrowUpRight size={12} aria-hidden />
          </Link>
        </span>
      </div>

      {/* KPI strip */}
      <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
        <KpiStat
          label="Y1 projected asset growth"
          value={y1AssetGrowth === null ? '—' : fmtPctSigned(y1AssetGrowth, 1)}
          hint={
            horizonAssetGrowth === null
              ? 'Derived from the stored path'
              : `${fmtPctSigned(horizonAssetGrowth, 1)} over ${horizon}Y · derived from path`
          }
          sparkline={<Sparkline data={assetPath} color={seriesColor(0)} />}
        />
        <KpiStat
          label={`Year-${horizon} CAR`}
          value={fmtPct(num(run.summary.year5CarPct), 2)}
          status={kpiTone(metricStatus(run, 'year5_car_pct'))}
          hint={`${regShort()} minimum ${fmtPct(carThreshold, 0)}`}
          sparkline={
            <Sparkline
              data={path.map((p) => num(p.carPct))}
              color={seriesColor(1)}
            />
          }
        />
        <KpiStat
          label="Average ROE"
          value={fmtPct(num(run.summary.avgRoePct), 2)}
          hint={`${horizon}-year average return on equity`}
          sparkline={
            <Sparkline
              data={path.filter((p) => p.roePct !== null).map((p) => num(p.roePct))}
              color={seriesColor(2)}
            />
          }
        />
        <KpiStat
          label="Cumulative net income"
          value={fmtCurrency(num(run.summary.cumulativeNetIncome))}
          hint={`Sum of projected Y1–Y${horizon} profit after tax`}
          sparkline={
            <Sparkline
              data={path.filter((p) => p.year > 0).map((p) => num(p.netIncome))}
              color={seriesColor(3)}
            />
          }
        />
      </div>

      {/* Projection chart + composition */}
      <div className="grid grid-cols-1 xl:grid-cols-5 gap-6">
        <ChartFrame
          className="xl:col-span-3"
          title="Balance-sheet projection"
          subtitle={`Total assets, liabilities, and equity over the ${horizon}-year horizon`}
          height={320}
          footer={
            <>
              <span>
                Liabilities derived as total assets − equity from the stored
                path (balance-sheet identity).
              </span>
              {adverse && (
                <span>
                  Shaded band spans base ↔ adverse total assets ·{' '}
                  {scenarioLabel(adverse.scenarioCode)} run{' '}
                  <span className="font-mono">{adverse.id.slice(0, 8)}</span>
                </span>
              )}
            </>
          }
        >
          <ProjectionChart data={projectionData} hasBand={Boolean(adverse)} />
        </ChartFrame>

        <ChartFrame
          className="xl:col-span-2"
          title="Asset composition"
          subtitle={`Loans, securities, and cash across the horizon · ${currencyCode()} millions`}
          height={320}
        >
          <BalanceSheetProjectionChart data={compositionData} height={320} />
        </ChartFrame>
      </div>

      {/* Waterfall */}
      <WaterfallSection run={run} />

      {/* Horizon table */}
      <SectionCard
        title={`${horizon}-year projection path`}
        subtitle="Annual balance-sheet and P&L path with period-over-period deltas"
        noPadding
        computedAt={computedAt}
      >
        <HorizonTable path={path} />
      </SectionCard>

      {/* Regulatory ratio paths */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <ChartFrame
          title="CAR path"
          subtitle={`${regShort()} minimum ${fmtPct(carThreshold, 0)}`}
          height={240}
        >
          <RatioPathChart
            data={path.map((p) => ({ label: yearLabel(p), value: num(p.carPct) }))}
            threshold={carThreshold}
            thresholdLabel={`${regShort()} min ${fmtPct(carThreshold, 0)}`}
            color={seriesColor(0)}
            label="CAR"
          />
        </ChartFrame>
        <ChartFrame
          title="LCR path"
          subtitle={`${regShort()} minimum ${fmtPct(lcrThreshold, 0)}`}
          height={240}
        >
          <RatioPathChart
            data={path.map((p) => ({ label: yearLabel(p), value: num(p.lcrPct) }))}
            threshold={lcrThreshold}
            thresholdLabel={`${regShort()} min ${fmtPct(lcrThreshold, 0)}`}
            color={seriesColor(1)}
            label="LCR"
          />
        </ChartFrame>
        <ChartFrame
          title="NSFR path"
          subtitle={`${regShort()} minimum ${fmtPct(nsfrThreshold, 0)}`}
          height={240}
        >
          <RatioPathChart
            data={path.map((p) => ({ label: yearLabel(p), value: num(p.nsfrPct) }))}
            threshold={nsfrThreshold}
            thresholdLabel={`${regShort()} min ${fmtPct(nsfrThreshold, 0)}`}
            color={seriesColor(2)}
            label="NSFR"
          />
        </ChartFrame>
      </div>

      {/* Validations */}
      <SectionCard
        title="Validations"
        subtitle="Projection integrity and regulatory rule evaluation persisted on the run"
        noPadding
        computedAt={computedAt}
      >
        <ValidationList validations={run.validations} />
      </SectionCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Waterfall — period-over-period delta decomposition of the persisted path
// ---------------------------------------------------------------------------

function WaterfallSection({ run }: { run: ForecastRunRead }) {
  const years = run.path.filter((p) => p.year > 0).map((p) => p.year);
  const [year, setYear] = useState(years[0] ?? 1);

  const steps = useMemo<WaterfallStep[] | null>(() => {
    const opening = run.path.find((p) => p.year === year - 1);
    const closing = run.path.find((p) => p.year === year);
    if (!opening || !closing) return null;

    const deltas: WaterfallStep[] = [
      { kind: 'total', label: `Opening (Y${year - 1})`, value: num(opening.totalAssets) },
      { kind: 'delta', label: 'Loans Δ', value: num(closing.loans) - num(opening.loans) },
      {
        kind: 'delta',
        label: 'Securities Δ',
        value: num(closing.securities) - num(opening.securities),
      },
      { kind: 'delta', label: 'Cash Δ', value: num(closing.cash) - num(opening.cash) },
    ];
    // Residual between the stored totals and the three tracked components —
    // shown only when the identity doesn't close exactly.
    const explained = deltas
      .filter((s) => s.kind === 'delta')
      .reduce((sum, s) => sum + s.value, 0);
    const residual =
      num(closing.totalAssets) - num(opening.totalAssets) - explained;
    if (Math.abs(residual) > 0.005) {
      deltas.push({ kind: 'delta', label: 'Other Δ', value: residual });
    }
    deltas.push({
      kind: 'total',
      label: `Closing (Y${year})`,
      value: num(closing.totalAssets),
    });
    return deltas;
  }, [run.path, year]);

  if (!steps) return null;

  const netChange =
    num(run.path.find((p) => p.year === year)?.totalAssets) -
    num(run.path.find((p) => p.year === year - 1)?.totalAssets);

  return (
    <ChartFrame
      title="Asset waterfall"
      subtitle={`Opening → growth / run-off → closing total assets for Y${year}`}
      height={300}
      actions={
        <div className="inline-flex gap-1 bg-surface p-1 rounded">
          {years.map((y) => (
            <button
              key={y}
              type="button"
              onClick={() => setYear(y)}
              className={`px-2.5 py-1 rounded text-caption font-medium ${
                y === year ? 'bg-surface-raised text-navy shadow-sm' : 'text-slate hover:text-navy'
              }`}
            >
              Y{y}
            </button>
          ))}
        </div>
      }
      footer={
        <>
          <span>
            Derivation: period-over-period delta decomposition of the persisted
            annual path — opening and closing bars are stored totals; the delta
            bars are Δloans, Δsecurities, and Δcash between the two stored years.
          </span>
          <span className="inline-flex items-center gap-1.5 ml-auto">
            Net change
            <DeltaBadge
              value={netChange / 1_000_000}
              suffix={`M ${currencyCode()}`}
              decimals={1}
            />
          </span>
        </>
      }
    >
      <WaterfallChart steps={steps} />
    </ChartFrame>
  );
}

// ---------------------------------------------------------------------------
// Horizon table with per-period deltas
// ---------------------------------------------------------------------------

type HorizonRow = {
  point: ProjectionYearRead;
  assetGrowthPct: number | null;
  niiGrowthPct: number | null;
};

function DeltaUnder({ value }: { value: number | null }) {
  if (value === null) return null;
  return (
    <span className="block">
      <DeltaBadge value={value} suffix="%" decimals={1} />
    </span>
  );
}

const horizonColumns: Column<HorizonRow>[] = [
  {
    key: 'year',
    header: 'Year',
    width: '11%',
    render: (r) => (
      <span className="font-medium text-navy">
        Y{r.point.year}{' '}
        <span className="font-mono text-caption text-slate">
          {r.point.periodLabel}
        </span>
      </span>
    ),
  },
  {
    key: 'assets',
    header: 'Assets · Δ YoY',
    numeric: true,
    render: (r) => (
      <>
        {fmtCurrency(num(r.point.totalAssets))}
        <DeltaUnder value={r.assetGrowthPct} />
      </>
    ),
  },
  {
    key: 'loans',
    header: 'Loans',
    numeric: true,
    render: (r) => fmtCurrency(num(r.point.loans)),
  },
  {
    key: 'deposits',
    header: 'Deposits',
    numeric: true,
    render: (r) => fmtCurrency(num(r.point.deposits)),
  },
  {
    key: 'equity',
    header: 'Equity',
    numeric: true,
    render: (r) => fmtCurrency(num(r.point.equity)),
  },
  {
    key: 'nii',
    header: 'NII · Δ YoY',
    numeric: true,
    render: (r) =>
      r.point.year === 0 ? (
        '—'
      ) : (
        <>
          {fmtCurrency(num(r.point.nii))}
          <DeltaUnder value={r.niiGrowthPct} />
        </>
      ),
  },
  {
    key: 'netIncome',
    header: 'Net income',
    numeric: true,
    render: (r) =>
      r.point.year === 0 ? '—' : fmtCurrency(num(r.point.netIncome)),
  },
  {
    key: 'roe',
    header: 'ROE',
    numeric: true,
    render: (r) =>
      r.point.roePct === null ? '—' : fmtPct(num(r.point.roePct), 2),
  },
  {
    key: 'car',
    header: 'CAR',
    numeric: true,
    render: (r) => (
      <span
        className={num(r.point.carPct) < 10 ? 'text-critical font-medium' : undefined}
      >
        {fmtPct(num(r.point.carPct), 2)}
      </span>
    ),
  },
  {
    key: 'lcr',
    header: 'LCR',
    numeric: true,
    render: (r) => (
      <span
        className={num(r.point.lcrPct) < 100 ? 'text-critical font-medium' : undefined}
      >
        {fmtPct(num(r.point.lcrPct), 1)}
      </span>
    ),
  },
  {
    key: 'nsfr',
    header: 'NSFR',
    numeric: true,
    render: (r) => (
      <span
        className={num(r.point.nsfrPct) < 100 ? 'text-critical font-medium' : undefined}
      >
        {fmtPct(num(r.point.nsfrPct), 1)}
      </span>
    ),
  },
];

function HorizonTable({ path }: { path: ProjectionYearRead[] }) {
  const rows: HorizonRow[] = path.map((point, i) => {
    const prev = i > 0 ? path[i - 1] : null;
    return {
      point,
      assetGrowthPct: yoyPct(
        num(point.totalAssets),
        prev ? num(prev.totalAssets) : null
      ),
      niiGrowthPct:
        point.year > 1 && prev
          ? yoyPct(num(point.nii), num(prev.nii))
          : null,
    };
  });
  return <DataTable columns={horizonColumns} rows={rows} density="compact" />;
}
