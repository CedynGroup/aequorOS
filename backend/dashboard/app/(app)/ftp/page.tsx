'use client';

import { Info, Loader2, Zap } from 'lucide-react';
import type {
  FtpBranchRead,
  FtpNmdSegmentRead,
  FtpProductRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import StatusPill from '@/components/ui/StatusPill';
import RunBadge from '@/components/ui/RunBadge';
import ValidationList from '@/components/ui/ValidationList';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import RatioHistoryChart from '@/components/charts/RatioHistoryChart';
import YieldCurveChart, { type CurvePoint } from '@/components/charts/YieldCurveChart';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useFtpDashboard,
  useRegulatoryRun,
  useRunAllFtpScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate, labelize, num, statusTone } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtPct } from '@/lib/format';

export default function FtpDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useFtpDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runAll = useRunAllFtpScenarios(bankId);

  const data = dashboard.data;
  const m = data?.metrics;

  const marginFloor = num(m?.minProductMarginPct);
  const nim = num(m?.portfolioNimPct);
  const nimTone = nim >= marginFloor ? 'compliant' : 'breach';

  const curvePoints: CurvePoint[] = (data?.curve ?? []).map((c) => ({
    tenor: c.tenorLabel,
    baseYield: num(c.baseYieldPct),
    ftpRate: num(c.ftpRatePct),
  }));

  const trendPoints = (data?.trend ?? []).map((p) => ({
    month: p.label,
    value: num(p.portfolioNimPct),
    stored: p.stored,
  }));
  const hasInlineTrendPoints = (data?.trend ?? []).some((p) => !p.stored);

  const runAllButton = (
    <button
      type="button"
      disabled={runAll.isPending || !periodId}
      onClick={() => periodId && runAll.mutate({ reportingPeriodId: periodId })}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-white bg-navy rounded-md hover:bg-navy-700 disabled:opacity-60"
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
          { label: 'Funds Transfer Pricing' },
          { label: 'FTP Dashboard' },
        ]}
        title="Funds Transfer Pricing"
        subtitle="Match-funded FTP curve · Product & branch profitability · NMD behaviouralisation"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="ftp"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
            {latestRun.data && <RunBadge run={latestRun.data} />}
            {runAllButton}
          </div>
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && m && (
          <div className="px-8 py-6 space-y-6">
            {!data.stored && (
              <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-3.5 flex items-start gap-3">
                <Info size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <p className="text-body text-navy/85 leading-relaxed">
                  Showing a live computation for this period — run all
                  scenarios to persist auditable regulatory runs for the rate
                  and funding-stress overlays.
                </p>
              </div>
            )}

            {/* NIM gauge + headline stats */}
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
              <RatioGauge
                label="Portfolio net interest margin"
                value={nim}
                threshold={marginFloor}
                status={statusTone(nimTone)}
                decimals={2}
                thresholdLabel="Minimum margin floor"
                higherIsBetter
              />
              <StatCard
                label="Products below margin floor"
                value={`${m.productsBelowMinMargin} of ${m.totalProducts}`}
                note={
                  m.productsBelowMinMargin > 0
                    ? 'Match-funded margin below the floor'
                    : 'All products clear the margin floor'
                }
                tone={m.productsBelowMinMargin > 0 ? 'amber' : undefined}
              />
              <StatCard
                label="NMD core funding"
                value={fmtPct(num(m.nmdCorePct), 1)}
                note={`Policy band ${num(m.nmdCoreMinPct).toFixed(0)}–${num(
                  m.nmdCoreMaxPct
                ).toFixed(0)}% of balances`}
                tone={
                  m.nmdCoreStatus === 'red'
                    ? 'critical'
                    : m.nmdCoreStatus === 'amber'
                    ? 'amber'
                    : undefined
                }
              />
            </div>

            {/* Yield curve */}
            <Card>
              <CardHeader
                title="FTP funding curve"
                subtitle={`Base market yield vs assigned FTP rate by tenor · Blended FTP ${fmtPct(
                  num(m.blendedAssignedFtpPct),
                  2
                )}`}
              />
              <CardBody>
                <YieldCurveChart data={curvePoints} />
              </CardBody>
            </Card>

            {/* Product profitability */}
            <Card>
              <CardHeader
                title="Product profitability"
                subtitle="Match-funded net margin by product · Below-floor products flagged"
                action={
                  <StatusPill
                    tone={m.productsBelowMinMargin > 0 ? 'amber' : 'success'}
                  >
                    {m.productsBelowMinMargin} below floor
                  </StatusPill>
                }
              />
              <CardBody className="p-0">
                <ProductTable rows={data.products} />
              </CardBody>
            </Card>

            {/* Branch ranking */}
            <Card>
              <CardHeader
                title="Branch profitability ranking"
                subtitle={`FTP-adjusted NIM by branch · Total contribution ${fmtCurrency(
                  num(m.totalBranchContributionGhs),
                  'GHS'
                )}`}
              />
              <CardBody className="p-0">
                <BranchTable rows={data.branches} />
              </CardBody>
            </Card>

            {/* NMD split */}
            <Card>
              <CardHeader
                title="Non-maturing deposit behaviouralisation"
                subtitle="Core vs volatile split, effective duration, and policy-band status"
              />
              <CardBody className="grid grid-cols-1 md:grid-cols-2 gap-5">
                {data.nmdSegments.map((seg) => (
                  <NmdCard key={seg.segment} seg={seg} />
                ))}
              </CardBody>
            </Card>

            {/* Trend */}
            <Card>
              <CardHeader
                title="Portfolio NIM — 12-period trend"
                subtitle="Trailing-year path of the FTP-adjusted portfolio margin"
                action={
                  <StatusPill tone={statusTone(nimTone)}>
                    {fmtPct(nim, 2)} NIM
                  </StatusPill>
                }
              />
              <CardBody>
                {trendPoints.length > 0 ? (
                  <>
                    <RatioHistoryChart
                      data={trendPoints}
                      threshold={marginFloor}
                      yMin={Math.floor(
                        Math.min(...trendPoints.map((p) => p.value), marginFloor) - 1
                      )}
                      yMax={Math.ceil(
                        Math.max(...trendPoints.map((p) => p.value)) + 2
                      )}
                      color="#0A2540"
                      label="Portfolio NIM"
                    />
                    {hasInlineTrendPoints && (
                      <p className="mt-2 text-caption text-slate">
                        Hollow points are computed inline — run all scenarios
                        on those periods to persist them.
                      </p>
                    )}
                  </>
                ) : (
                  <EmptyState
                    Icon={Zap}
                    title="No trend history"
                    description="Run all scenarios to build the per-period NIM trend."
                    action={runAllButton}
                  />
                )}
              </CardBody>
            </Card>

            {/* Validations */}
            <Card>
              <CardHeader
                title="Validations"
                subtitle="FTP curve, margin, and NMD policy rule evaluation for this period"
              />
              <CardBody className="p-0">
                <ValidationList validations={data.validations} />
              </CardBody>
            </Card>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}

function ProductTable({ rows }: { rows: FtpProductRead[] }) {
  const columns: Column<FtpProductRead>[] = [
    {
      key: 'product',
      header: 'Product',
      render: (r) => (
        <span className={r.belowMinMargin ? 'text-critical font-medium' : undefined}>
          {labelize(r.product)}
        </span>
      ),
      width: '20%',
    },
    {
      key: 'category',
      header: 'Category',
      render: (r) => (
        <StatusPill tone={r.category === 'asset' ? 'action' : 'slate'}>
          {labelize(r.category)}
        </StatusPill>
      ),
    },
    {
      key: 'customer',
      header: 'Customer rate',
      numeric: true,
      render: (r) => fmtPct(num(r.customerRatePct), 2),
    },
    {
      key: 'ftp',
      header: 'FTP rate',
      numeric: true,
      render: (r) => fmtPct(num(r.ftpRatePct), 2),
    },
    {
      key: 'opex',
      header: 'Opex',
      numeric: true,
      render: (r) => fmtPct(num(r.operatingCostPct), 2),
    },
    {
      key: 'ecl',
      header: 'ECL',
      numeric: true,
      render: (r) => fmtPct(num(r.expectedCreditLossPct), 2),
    },
    {
      key: 'capital',
      header: 'Capital charge',
      numeric: true,
      render: (r) => fmtPct(num(r.capitalChargePct), 2),
    },
    {
      key: 'margin',
      header: 'Net margin',
      numeric: true,
      render: (r) => {
        const v = num(r.netMarginPct);
        return (
          <span className={r.belowMinMargin ? 'text-critical font-medium' : undefined}>
            {fmtPct(v, 2)}
          </span>
        );
      },
    },
  ];
  return (
    <DataTable
      columns={columns}
      rows={rows}
      density="compact"
      totalsRowMatcher={(r) => r.belowMinMargin}
    />
  );
}

function BranchTable({ rows }: { rows: FtpBranchRead[] }) {
  const columns: Column<FtpBranchRead>[] = [
    {
      key: 'rank',
      header: '#',
      render: (r) => <span className="font-mono text-slate">{r.rank}</span>,
      width: '6%',
    },
    { key: 'branch', header: 'Branch', render: (r) => labelize(r.branch), width: '22%' },
    {
      key: 'deposits',
      header: 'Deposits',
      numeric: true,
      render: (r) => fmtCurrency(num(r.depositsGhs), 'GHS'),
    },
    {
      key: 'loans',
      header: 'Loans',
      numeric: true,
      render: (r) => fmtCurrency(num(r.loansGhs), 'GHS'),
    },
    {
      key: 'nim',
      header: 'FTP-adjusted NIM',
      numeric: true,
      render: (r) => fmtPct(num(r.ftpAdjustedNimPct), 2),
    },
    {
      key: 'contribution',
      header: 'Net contribution',
      numeric: true,
      render: (r) => fmtCurrencySigned(num(r.netContributionGhs), 'GHS'),
    },
  ];
  return <DataTable columns={columns} rows={rows} density="compact" />;
}

function NmdCard({ seg }: { seg: FtpNmdSegmentRead }) {
  const core = num(seg.corePct);
  const volatile = num(seg.volatilePct);
  return (
    <div className="card p-5 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-h3 text-navy">{labelize(seg.segment)}</p>
          <p className="text-caption text-slate">
            Balance {fmtCurrency(num(seg.balanceGhs), 'GHS')} · Effective duration{' '}
            {num(seg.effectiveDurationYears).toFixed(2)}y
          </p>
        </div>
        <StatusPill tone={seg.withinPolicy ? 'compliant' : 'breach'}>
          {seg.withinPolicy ? 'Within policy' : 'Breach'}
        </StatusPill>
      </div>

      <div className="h-2.5 rounded-full overflow-hidden bg-surface flex">
        <div className="h-full bg-navy" style={{ width: `${core}%` }} aria-hidden />
        <div
          className="h-full bg-action/50"
          style={{ width: `${volatile}%` }}
          aria-hidden
        />
      </div>

      <div className="grid grid-cols-2 gap-4">
        <div>
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Core
          </p>
          <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
            {fmtPct(core, 1)}
          </p>
          <p className="text-caption text-slate">
            {fmtCurrency(num(seg.coreAmountGhs), 'GHS')} · FTP{' '}
            {fmtPct(num(seg.coreFtpPct), 2)}
          </p>
        </div>
        <div>
          <p className="text-micro font-medium uppercase tracking-wider text-slate">
            Volatile
          </p>
          <p className="mt-1 font-mono text-h2 text-navy tabular-nums">
            {fmtPct(volatile, 1)}
          </p>
          <p className="text-caption text-slate">
            {fmtCurrency(num(seg.volatileAmountGhs), 'GHS')} · FTP{' '}
            {fmtPct(num(seg.volatileFtpPct), 2)}
          </p>
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label,
  value,
  note,
  tone,
}: {
  label: string;
  value: string;
  note?: string;
  tone?: 'amber' | 'critical';
}) {
  const valueColor =
    tone === 'critical'
      ? 'text-critical'
      : tone === 'amber'
      ? 'text-warning'
      : 'text-navy';
  return (
    <div className="card p-5 h-full flex flex-col gap-2">
      <p className="text-caption font-medium text-slate uppercase tracking-wider">
        {label}
      </p>
      <p className={`font-mono text-h1 tabular-nums ${valueColor}`}>{value}</p>
      {note && <p className="text-caption text-slate leading-snug mt-auto">{note}</p>}
    </div>
  );
}
