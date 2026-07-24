'use client';

import Link from 'next/link';
import { FileText, Info, Loader2, PlayCircle } from 'lucide-react';
import type { LiquidityDashboardLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import Sparkline from '@/components/ui/Sparkline';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioTrendChart from '@/components/liquidity/charts/RatioTrendChart';
import NetOutflowChart from '@/components/liquidity/charts/NetOutflowChart';
import { runComputedAt, runThresholds } from '@/components/liquidity/runData';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateRegulatoryRun,
  useLiquidityDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtPct, regShort, centralBankName } from '@/lib/format';

type LineRow = {
  item: string;
  balanceGHS: number | null;
  ratePct: number | null;
  weightedGHS: number;
  isTotal?: boolean;
};

function toRow(line: LiquidityDashboardLineRead): LineRow {
  return {
    item: line.description,
    balanceGHS: line.exposureAmount === null ? null : num(line.exposureAmount),
    ratePct: line.ratePct === null ? null : num(line.ratePct),
    weightedGHS: num(line.weightedAmount),
  };
}

function lineColumns(rateHeader: string, weightedHeader: string): Column<LineRow>[] {
  return [
    { key: 'item', header: 'Category', render: (r) => r.item, width: '46%' },
    {
      key: 'balance',
      header: 'Balance (GHS)',
      numeric: true,
      render: (r) =>
        r.balanceGHS === null ? '—' : fmtCurrency(r.balanceGHS),
    },
    {
      key: 'rate',
      header: rateHeader,
      numeric: true,
      render: (r) => (r.ratePct === null ? '—' : `${r.ratePct.toFixed(0)}%`),
    },
    {
      key: 'weighted',
      header: weightedHeader,
      numeric: true,
      render: (r) => fmtCurrency(r.weightedGHS),
    },
  ];
}

export default function LiquidityCockpit() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useLiquidityDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = dashboard.data;
  const run = latestRun.data;

  // Regulatory floors from the stored run's parameter snapshot; the standard
  // BoG CRD values are the fallback before a run is persisted.
  const thresholds = runThresholds(run);
  const lcrMin = thresholds['lcr_min'] ?? 100;
  const lcrRedFloor = thresholds['lcr_amber_floor'] ?? 90;
  const nsfrMin = thresholds['nsfr_min'] ?? 100;
  const nsfrRedFloor = thresholds['nsfr_amber_floor'] ?? nsfrMin;

  const outflowRows = (data?.outflows ?? []).map(toRow);
  const inflowRows = (data?.inflows ?? []).map(toRow);
  const totalOutflows = outflowRows.reduce((s, r) => s + r.weightedGHS, 0);
  // Identity: net outflows = total outflows − capped inflows.
  const cappedInflows = data
    ? totalOutflows - num(data.metrics.netOutflows30dGhs)
    : 0;
  const capNote = data?.validations.find(
    (v) => v.ruleCode === 'inflow_cap_applied'
  );
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);

  const hqlaTotal = num(data?.metrics.hqlaTotalGhs);
  const lcrTrend = (data?.trend ?? []).map((p) => num(p.lcrPct));
  const nsfrTrend = (data?.trend ?? []).map((p) => num(p.nsfrPct));
  const periodDelta = (series: number[]): number | undefined =>
    series.length >= 2
      ? series[series.length - 1] - series[series.length - 2]
      : undefined;
  const lcrDelta = periodDelta(lcrTrend);
  const nsfrDelta = periodDelta(nsfrTrend);

  const computedAt = runComputedAt(run);
  const provenance = data ? (
    <span>
      {data.stored
        ? 'Stored baseline run'
        : 'Live computation — run baseline to persist'}
    </span>
  ) : undefined;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk' },
          { label: 'Cockpit' },
        ]}
        title="Liquidity Cockpit"
        subtitle={`Basel III LCR & NSFR per ${centralBankName()} CRD framework · 30-day stressed horizon`}
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="liquidity"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
            {run && <RunBadge run={run} />}
            <Link
              href="/submissions/returns?code=BSD3"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
            >
              <FileText size={13} aria-hidden />
              Official returns →
            </Link>
            <button
              type="button"
              disabled={runBaseline.isPending || !periodId}
              onClick={() =>
                periodId &&
                runBaseline.mutate({
                  module: 'liquidity',
                  reportingPeriodId: periodId,
                  scenarioCode: 'baseline',
                })
              }
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {runBaseline.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <PlayCircle size={13} aria-hidden />
              )}
              Run baseline
            </button>
          </div>
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
            {!data.stored && (
              <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-3.5 flex items-start gap-3">
                <Info size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <p className="text-body text-navy/85 leading-relaxed">
                  Showing a live computation for this period — run baseline to
                  persist an auditable regulatory run.
                </p>
              </div>
            )}

            {/* Headline gauges + component KPIs */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
              <div className="lg:col-span-2">
                <RatioGauge
                  label="Liquidity Coverage Ratio"
                  value={num(data.metrics.lcrPct)}
                  threshold={lcrMin}
                  internalBuffer={lcrRedFloor}
                  bufferLabel="Red floor"
                  status={statusTone(data.metrics.lcrStatus)}
                  decimals={2}
                />
              </div>
              <KpiStat
                label="HQLA stock"
                value={fmtCurrency(hqlaTotal)}
                status={
                  data.metrics.lcrStatus === 'red'
                    ? 'crit'
                    : data.metrics.lcrStatus === 'amber'
                    ? 'warn'
                    : 'ok'
                }
                delta={lcrDelta}
                deltaSuffix=" pts LCR"
                hint="Post-haircut weighted"
              />
              <KpiStat
                label="30-day net outflows"
                value={fmtCurrency(num(data.metrics.netOutflows30dGhs))}
                hint="Outflows − capped inflows"
              />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
              <div className="lg:col-span-2">
                <RatioGauge
                  label="Net Stable Funding Ratio"
                  value={num(data.metrics.nsfrPct)}
                  threshold={nsfrMin}
                  status={statusTone(data.metrics.nsfrStatus)}
                  decimals={2}
                />
              </div>
              <KpiStat
                label="Available stable funding"
                value={fmtCurrency(num(data.metrics.asfTotalGhs))}
                delta={nsfrDelta}
                deltaSuffix=" pts NSFR"
                hint="Liability-side weighting"
              />
              <KpiStat
                label="Required stable funding"
                value={fmtCurrency(num(data.metrics.rsfTotalGhs))}
                hint="Asset-side weighting"
              />
            </div>

            {/* Regulatory floors — LCR & NSFR are floor limits (direction above) */}
            <SectionCard
              title="Regulatory floors"
              subtitle={`${regShort()} CRD thresholds from the active parameter set — green ≥ minimum, amber down to the red floor`}
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={provenance}
            >
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-10 gap-y-5">
                <LimitBar
                  label={
                    <span className="inline-flex items-center gap-2">
                      LCR
                      <Sparkline data={lcrTrend} width={64} height={16} />
                    </span>
                  }
                  value={num(data.metrics.lcrPct)}
                  limit={lcrRedFloor}
                  warnAt={lcrMin}
                  direction="above"
                  unit="%"
                  limitLabel="Red floor"
                  warnLabel={`${regShort()} minimum`}
                  format={(v) => v.toFixed(1)}
                />
                <LimitBar
                  label={
                    <span className="inline-flex items-center gap-2">
                      NSFR
                      <Sparkline data={nsfrTrend} width={64} height={16} />
                    </span>
                  }
                  value={num(data.metrics.nsfrPct)}
                  limit={nsfrRedFloor}
                  warnAt={nsfrMin}
                  direction="above"
                  unit="%"
                  limitLabel={nsfrRedFloor === nsfrMin ? `${regShort()} minimum` : 'Red floor'}
                  warnLabel={`${regShort()} minimum`}
                  format={(v) => v.toFixed(1)}
                />
              </div>
            </SectionCard>

            {/* Trend + net-outflow decomposition */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <ChartFrame
                className="lg:col-span-2"
                title="LCR & NSFR — reporting-period trend"
                subtitle={`Ratios across ${data.trend.length} reporting periods`}
                height={260}
                actions={
                  <StatusPill tone="success">
                    LCR compliant{' '}
                    {data.trend.filter((p) => num(p.lcrPct) >= lcrMin).length} of{' '}
                    {data.trend.length}
                  </StatusPill>
                }
                footer={
                  hasInlineTrendPoints ? (
                    <span>
                      Hollow points are computed inline — run baseline on those
                      periods to persist them.
                    </span>
                  ) : (
                    <span>All trend points are stored baseline runs.</span>
                  )
                }
              >
                <RatioTrendChart
                  data={data.trend.map((p) => ({
                    label: p.label,
                    primary: num(p.lcrPct),
                    secondary: num(p.nsfrPct),
                    stored: p.stored,
                  }))}
                  threshold={lcrMin}
                  thresholdLabel="Min"
                  redFloor={lcrRedFloor}
                  redFloorLabel="Red floor"
                  primaryLabel="LCR"
                  secondaryLabel="NSFR"
                  height={260}
                />
              </ChartFrame>

              <ChartFrame
                title="Net-outflow decomposition"
                subtitle="Weighted 30-day outflows by category vs capped inflows"
                height={260}
                footer={
                  capNote ? <span>{capNote.message}</span> : undefined
                }
              >
                <NetOutflowChart
                  outflows={outflowRows.map((r) => ({
                    name: r.item,
                    weighted: r.weightedGHS,
                  }))}
                  cappedInflows={cappedInflows}
                  netOutflows={num(data.metrics.netOutflows30dGhs)}
                  height={260}
                />
              </ChartFrame>
            </div>

            {/* Outflow & inflow tables */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SectionCard
                title="Cash outflows"
                subtitle={`30-day stressed runoff per ${regShort()} CRD weights`}
                noPadding
                computedAt={computedAt}
                runBadge={run ? <RunBadge run={run} /> : undefined}
                footer={provenance}
              >
                <DataTable
                  columns={lineColumns('Runoff %', 'Stressed outflow')}
                  rows={[
                    ...outflowRows,
                    {
                      item: 'TOTAL CASH OUTFLOWS',
                      balanceGHS: outflowRows.reduce(
                        (s, r) => s + (r.balanceGHS ?? 0),
                        0
                      ),
                      ratePct: null,
                      weightedGHS: totalOutflows,
                      isTotal: true,
                    },
                  ]}
                  totalsRowMatcher={(r) => Boolean(r.isTotal)}
                />
              </SectionCard>

              <SectionCard
                title="Cash inflows"
                subtitle="Capped at 75% of outflows per Basel III"
                noPadding
                computedAt={computedAt}
                runBadge={run ? <RunBadge run={run} /> : undefined}
                footer={capNote ? <span>{capNote.message}</span> : provenance}
              >
                <DataTable
                  columns={lineColumns('Inflow %', 'Weighted inflow')}
                  rows={[
                    ...inflowRows,
                    {
                      item: 'GROSS INFLOWS',
                      balanceGHS: inflowRows.reduce(
                        (s, r) => s + (r.balanceGHS ?? 0),
                        0
                      ),
                      ratePct: null,
                      weightedGHS: inflowRows.reduce(
                        (s, r) => s + r.weightedGHS,
                        0
                      ),
                      isTotal: true,
                    },
                    {
                      item: 'CAPPED INFLOWS (min of gross, 75% of outflows)',
                      balanceGHS: null,
                      ratePct: null,
                      weightedGHS: cappedInflows,
                      isTotal: true,
                    },
                  ]}
                  totalsRowMatcher={(r) => Boolean(r.isTotal)}
                />
              </SectionCard>
            </div>

            {/* Validations */}
            <SectionCard
              title="Validations"
              subtitle="Regulatory rule evaluation for this period"
              noPadding
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
              footer={provenance}
            >
              <ValidationList validations={data.validations} />
            </SectionCard>

            {/* Compliance summary line */}
            <p className="text-caption text-slate flex items-center gap-2 flex-wrap">
              Net outflows = Outflows{' '}
              <span className="font-mono text-navy">
                {fmtCurrency(totalOutflows)}
              </span>{' '}
              − min(Gross inflows, 75% × Outflows){' '}
              <span className="font-mono text-navy">
                {fmtCurrency(cappedInflows)}
              </span>{' '}
              ={' '}
              <span className="font-mono font-medium text-navy">
                {fmtCurrency(num(data.metrics.netOutflows30dGhs))}
              </span>
              . LCR = HQLA{' '}
              <span className="font-mono text-navy">
                {fmtCurrency(hqlaTotal)}
              </span>{' '}
              / Net outflows ={' '}
              <span className="font-mono font-medium text-success">
                {fmtPct(num(data.metrics.lcrPct), 2)}
              </span>
              .
            </p>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
