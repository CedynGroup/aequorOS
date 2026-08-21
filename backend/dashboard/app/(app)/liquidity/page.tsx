'use client';

import Link from 'next/link';
import { ArrowUpRight } from 'lucide-react';
import type { LiquidityDashboardLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import Sparkline from '@/components/ui/Sparkline';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioTrendChart from '@/components/liquidity/charts/RatioTrendChart';
import NetOutflowChart from '@/components/liquidity/charts/NetOutflowChart';
import SdiLiquidityView from '@/components/liquidity/SdiLiquidityView';
import { runComputedAt, runThresholds } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import LiveEngineNote from '@/components/live/LiveEngineNote';
import {
  useCfpSummary,
  useEwiDashboard,
  useLiquidityDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { num, statusTone } from '@/lib/api/values';
import { currencyCode, fmtCurrency, fmtPct, regShort, centralBankName } from '@/lib/format';

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
      header: `Balance (${currencyCode()})`,
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

function escalationTone(
  escalation: string | undefined
): 'success' | 'amber' | 'critical' | 'slate' {
  if (escalation === 'cfp_active' || escalation === 'escalation') return 'critical';
  if (escalation === 'heightened_monitoring') return 'amber';
  return escalation ? 'success' : 'slate';
}

function escalationLabel(escalation: string | undefined): string {
  if (escalation === 'cfp_active') return 'CFP active';
  if (escalation === 'escalation') return 'Escalation';
  if (escalation === 'heightened_monitoring') return 'Heightened monitoring';
  return escalation === 'normal' ? 'Business as usual' : 'Awaiting EWI';
}

export default function LiquidityCockpit() {
  const { bank, moduleScope } = useBankContext();
  const bankId = bank?.id;
  const isSdi = moduleScope.institutionClass === 'sdi';

  const dashboard = useLiquidityDashboard(isSdi ? undefined : bankId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const ewis = useEwiDashboard(isSdi ? undefined : bankId);
  const cfp = useCfpSummary(isSdi ? undefined : bankId);

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
  const hqlaRows = data?.hqlaComposition ?? [];
  const largestHqla = hqlaRows.reduce<LiquidityDashboardLineRead | null>(
    (largest, line) =>
      largest === null || num(line.weightedAmount) > num(largest.weightedAmount) ? line : largest,
    null
  );
  const largestHqlaShare =
    largestHqla && hqlaTotal > 0 ? (num(largestHqla.weightedAmount) / hqlaTotal) * 100 : null;
  const lcrHeadroomGhs =
    hqlaTotal - num(data?.metrics.netOutflows30dGhs) * (lcrMin / 100);
  const nsfrSurplus = num(data?.metrics.asfTotalGhs) - num(data?.metrics.rsfTotalGhs);
  const ewi = ewis.data;
  const actionIndicators = ewi?.indicators.filter((item) => item.status === 'action') ?? [];
  const watchIndicators = ewi?.indicators.filter((item) => item.status === 'watch') ?? [];
  const approvedCfp = cfp.data?.approved ?? null;
  const fundingOptions = approvedCfp?.content.fundingOptions ?? [];
  const actionPlans = approvedCfp?.content.actionPlans ?? [];

  const computedAt = runComputedAt(run);
  const provenance = data ? (
    <span>
      Computed from current positions and the active parameter set
    </span>
  ) : undefined;

  if (isSdi) {
    return <SdiLiquidityView bankId={bankId} />;
  }

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
        action={data ? <LiveEngineNote live={data.live} stored={data.stored} /> : undefined}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">

            <SectionCard
              title="Liquidity posture"
              subtitle="Current compliance headroom, buffer concentration, early-warning state, and contingency readiness."
              computedAt={computedAt}
              footer={provenance}
            >
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-5 gap-4">
                <KpiStat
                  label="LCR headroom"
                  value={`${(num(data.metrics.lcrPct) - lcrMin).toFixed(1)} pp`}
                  status={data.metrics.lcrStatus === 'red' ? 'crit' : data.metrics.lcrStatus === 'amber' ? 'warn' : 'ok'}
                  hint={`${fmtCurrency(lcrHeadroomGhs)} above minimum requirement`}
                />
                <KpiStat
                  label="NSFR funding surplus"
                  value={fmtCurrency(nsfrSurplus)}
                  status={nsfrSurplus < 0 ? 'crit' : 'ok'}
                  hint={`${(num(data.metrics.nsfrPct) - nsfrMin).toFixed(1)} pp above minimum`}
                />
                <KpiStat
                  label="Largest HQLA concentration"
                  value={largestHqlaShare === null ? '—' : fmtPct(largestHqlaShare, 1)}
                  status={largestHqlaShare !== null && largestHqlaShare >= 75 ? 'warn' : 'ok'}
                  hint={largestHqla?.description ?? 'No HQLA instruments'}
                />
                <KpiStat
                  label="Early-warning posture"
                  value={actionIndicators.length > 0 ? `${actionIndicators.length} action` : watchIndicators.length > 0 ? `${watchIndicators.length} watch` : 'Normal'}
                  status={actionIndicators.length > 0 ? 'crit' : watchIndicators.length > 0 ? 'warn' : 'ok'}
                  hint={ewi ? escalationLabel(ewi.escalationState) : 'EWI view is not available yet'}
                />
                <KpiStat
                  label="CFP readiness"
                  value={approvedCfp ? `v${approvedCfp.version}` : 'No approved plan'}
                  status={approvedCfp ? (approvedCfp.approvalOverdue ? 'warn' : 'ok') : 'warn'}
                  hint={approvedCfp ? `${fundingOptions.length} funding options · ${actionPlans.length} actions` : 'Approval is required before activation'}
                />
              </div>
            </SectionCard>

            <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
              <SectionCard
                className="xl:col-span-2"
                title="Escalation and contingency readiness"
                subtitle="EWI classifications are calculated server-side; the CFP remains a Board-owned activation control."
              >
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <div className="border-r-0 md:border-r md:border-border-light md:pr-5">
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-body font-medium text-navy">Early-warning indicators</p>
                      <StatusPill tone={escalationTone(ewi?.escalationState)}>{escalationLabel(ewi?.escalationState)}</StatusPill>
                    </div>
                    {ewi ? (
                      <p className="mt-2 text-caption text-slate leading-relaxed">
                        {actionIndicators.length} action trigger{actionIndicators.length === 1 ? '' : 's'} · {watchIndicators.length} watch trigger{watchIndicators.length === 1 ? '' : 's'}.
                      </p>
                    ) : (
                      <p className="mt-2 text-caption text-slate">The EWI evaluation is awaiting a compatible live liquidity context.</p>
                    )}
                  </div>
                  <div>
                    <div className="flex items-center justify-between gap-3">
                      <p className="text-body font-medium text-navy">Contingency Funding Plan</p>
                      <StatusPill tone={approvedCfp ? approvedCfp.approvalOverdue ? 'amber' : 'success' : 'slate'}>{approvedCfp ? approvedCfp.approvalOverdue ? 'review overdue' : 'approved' : 'not approved'}</StatusPill>
                    </div>
                    <p className="mt-2 text-caption text-slate leading-relaxed">
                      {approvedCfp ? `Plan v${approvedCfp.version} has ${fundingOptions.length} documented funding option${fundingOptions.length === 1 ? '' : 's'} and ${actionPlans.length} action${actionPlans.length === 1 ? '' : 's'}.` : 'No Board-approved plan is available for activation.'}
                    </p>
                  </div>
                </div>
              </SectionCard>
              <SectionCard title="Control workspace" subtitle="Move from current posture to the relevant control without losing context.">
                <div className="space-y-2">
                  <Link href="/liquidity/buffer" className="flex items-center justify-between gap-3 border-b border-border-light pb-2 text-body text-navy hover:text-action">
                    Buffer concentration and haircuts <ArrowUpRight size={14} aria-hidden />
                  </Link>
                  <Link href="/liquidity/monitoring" className="flex items-center justify-between gap-3 border-b border-border-light py-2 text-body text-navy hover:text-action">
                    Thresholds and maturity monitoring <ArrowUpRight size={14} aria-hidden />
                  </Link>
                  <Link href="/liquidity/cfp" className="flex items-center justify-between gap-3 pt-2 text-body text-navy hover:text-action">
                    CFP actions and activation log <ArrowUpRight size={14} aria-hidden />
                  </Link>
                </div>
              </SectionCard>
            </div>

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
                      Hollow points are live computations — they solidify once
                      those periods’ results are stored.
                    </span>
                  ) : (
                    <span>All trend points come from stored results.</span>
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
