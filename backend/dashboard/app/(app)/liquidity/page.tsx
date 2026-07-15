'use client';

import Link from 'next/link';
import { FileText, Info, Loader2, PlayCircle } from 'lucide-react';
import type { LiquidityDashboardLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KPICard from '@/components/ui/KPICard';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import HQLAStackChart from '@/components/charts/HQLAStackChart';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateRegulatoryRun,
  useLiquidityDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtPct } from '@/lib/format';

const HQLA_COLORS = ['#0E8A4F', '#2D7FF9', '#1A4D5C', '#C97C00', '#5A6776'];

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
        r.balanceGHS === null ? '—' : fmtCurrency(r.balanceGHS, 'GHS'),
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
      render: (r) => fmtCurrency(r.weightedGHS, 'GHS'),
    },
  ];
}

export default function LCRDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useLiquidityDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = dashboard.data;
  const lcrThreshold = num(
    latestRun.data?.metricResults.find((m) => m.metricCode === 'lcr_pct')
      ?.thresholdMin ?? '100'
  );

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
  const hqlaBreakdown = (data?.hqlaComposition ?? []).map((line, i) => ({
    level: line.description,
    label: line.lineCode,
    shareGHS: num(line.weightedAmount),
    pct: hqlaTotal > 0 ? Math.round((num(line.weightedAmount) / hqlaTotal) * 100) : 0,
    color: HQLA_COLORS[i % HQLA_COLORS.length],
  }));

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk' },
          { label: 'LCR Dashboard' },
        ]}
        title="Liquidity Coverage Ratio"
        subtitle="Basel III LCR per Bank of Ghana CRD framework · 30-day stressed horizon"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            {latestRun.data && <RunBadge run={latestRun.data} />}
            <Link
              href="/liquidity/submission"
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-action border border-action/30 bg-action-light rounded-md hover:bg-action/10"
            >
              <FileText size={13} aria-hidden />
              Generate BoG return
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
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
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

            {/* Top row: ratio gauge + 2 KPIs */}
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
              <div className="lg:col-span-2">
                <RatioGauge
                  label="Liquidity Coverage Ratio"
                  value={num(data.metrics.lcrPct)}
                  threshold={lcrThreshold}
                  internalBuffer={90}
                  bufferLabel="Amber floor"
                  status={statusTone(data.metrics.lcrStatus)}
                  decimals={2}
                />
              </div>
              <KPICard
                label="HQLA stock"
                value={hqlaTotal / 1_000_000}
                prefix="GHS"
                suffix="M"
                decimals={1}
                status={statusTone(data.metrics.lcrStatus)}
              />
              <KPICard
                label="30-day net outflows"
                value={num(data.metrics.netOutflows30dGhs) / 1_000_000}
                prefix="GHS"
                suffix="M"
                decimals={1}
                status={statusTone(data.metrics.lcrStatus)}
              />
            </div>

            {/* 12-period trend + HQLA composition */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
              <Card className="lg:col-span-2">
                <CardHeader
                  title="LCR — 12-period trend"
                  subtitle="Reporting-period LCR across the trailing year"
                  action={
                    <StatusPill tone="success">
                      Compliant{' '}
                      {
                        data.trend.filter((p) => num(p.lcrPct) >= lcrThreshold)
                          .length
                      }{' '}
                      of {data.trend.length}
                    </StatusPill>
                  }
                />
                <CardBody>
                  <RatioHistoryChart
                    data={data.trend.map((p) => ({
                      month: p.label,
                      value: num(p.lcrPct),
                      stored: p.stored,
                    }))}
                    threshold={lcrThreshold}
                    internalBuffer={90}
                    color="#0E8A4F"
                    label="LCR"
                  />
                  {hasInlineTrendPoints && (
                    <p className="mt-2 text-caption text-slate">
                      Hollow points are computed inline — run baseline on those
                      periods to persist them.
                    </p>
                  )}
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="HQLA composition"
                  subtitle="Post-haircut weighted liquid assets"
                />
                <CardBody className="space-y-4">
                  <HQLAStackChart data={hqlaBreakdown} />
                  <ul className="space-y-2 text-caption pt-2 border-t border-border-light">
                    {hqlaBreakdown.map((h) => (
                      <li key={h.level} className="flex items-center gap-3">
                        <span
                          className="w-2 h-2 rounded-sm shrink-0"
                          style={{ background: h.color }}
                          aria-hidden
                        />
                        <span className="text-navy flex-1 truncate font-medium">
                          {h.level}
                        </span>
                        <span className="font-mono text-navy tabular-nums shrink-0">
                          {fmtCurrency(h.shareGHS, 'GHS')}
                        </span>
                        <span className="font-mono text-slate tabular-nums w-10 text-right shrink-0">
                          {h.pct}%
                        </span>
                      </li>
                    ))}
                  </ul>
                </CardBody>
              </Card>
            </div>

            {/* Outflow & inflow tables */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <Card>
                <CardHeader
                  title="Cash outflows"
                  subtitle="30-day stressed runoff per BoG CRD weights"
                />
                <CardBody className="p-0">
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
                </CardBody>
              </Card>

              <Card>
                <CardHeader
                  title="Cash inflows"
                  subtitle="Capped at 75% of outflows per Basel III"
                />
                <CardBody className="p-0">
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
                  {capNote && (
                    <p className="px-4 py-3 text-caption text-slate border-t border-border-light">
                      {capNote.message}
                    </p>
                  )}
                </CardBody>
              </Card>
            </div>

            {/* Validations */}
            <Card>
              <CardHeader
                title="Validations"
                subtitle="Regulatory rule evaluation for this period"
              />
              <CardBody className="p-0">
                <ValidationList validations={data.validations} />
              </CardBody>
            </Card>

            {/* Compliance summary line */}
            <p className="text-caption text-slate flex items-center gap-2 flex-wrap">
              Net outflows = Outflows{' '}
              <span className="font-mono text-navy">
                {fmtCurrency(totalOutflows, 'GHS')}
              </span>{' '}
              − min(Gross inflows, 75% × Outflows){' '}
              <span className="font-mono text-navy">
                {fmtCurrency(cappedInflows, 'GHS')}
              </span>{' '}
              ={' '}
              <span className="font-mono font-medium text-navy">
                {fmtCurrency(num(data.metrics.netOutflows30dGhs), 'GHS')}
              </span>
              . LCR = HQLA{' '}
              <span className="font-mono text-navy">
                {fmtCurrency(hqlaTotal, 'GHS')}
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
