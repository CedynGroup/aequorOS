'use client';

import { Loader2, PlayCircle } from 'lucide-react';
import type { CapitalLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { Card, CardHeader, CardBody } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useCreateRegulatoryRun,
  useRwaBreakdown,
} from '@/lib/api/hooks';
import { fmtDateUTC, num, shortId } from '@/lib/api/values';
import { fmtCurrency } from '@/lib/format';

type Row = {
  item: string;
  exposureGHS: number | null;
  weightPct: number | null;
  rwaGHS: number;
  isTotal?: boolean;
};

function toRow(line: CapitalLineRead): Row {
  return {
    item: line.description,
    exposureGHS: line.exposureAmount === null ? null : num(line.exposureAmount),
    weightPct: line.ratePct === null ? null : num(line.ratePct),
    rwaGHS: num(line.weightedAmount),
  };
}

function rwaColumns(
  itemHeader: string,
  weightHeader: string,
  amountHeader: string
): Column<Row>[] {
  return [
    { key: 'item', header: itemHeader, render: (r) => r.item, width: '44%' },
    {
      key: 'exposure',
      header: 'Exposure (GHS)',
      numeric: true,
      render: (r) =>
        r.exposureGHS === null ? '—' : fmtCurrency(r.exposureGHS, 'GHS'),
    },
    {
      key: 'weight',
      header: weightHeader,
      numeric: true,
      render: (r) =>
        r.weightPct === null ? '—' : `${r.weightPct.toFixed(0)}%`,
    },
    {
      key: 'rwa',
      header: amountHeader,
      numeric: true,
      render: (r) => fmtCurrency(r.rwaGHS, 'GHS'),
    },
  ];
}

export default function RWABreakdown() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const breakdown = useRwaBreakdown(bankId, periodId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = breakdown.data;
  const needsBaseline = isNoBaselineRunError(breakdown.error);

  const creditRows = (data?.creditLines ?? []).map(toRow);
  const marketRows = (data?.marketLines ?? []).map(toRow);
  const operationalRows = (data?.operationalLines ?? []).map(toRow);

  const runBaselineButton = (
    <button
      type="button"
      disabled={runBaseline.isPending || !periodId}
      onClick={() =>
        periodId &&
        runBaseline.mutate({
          module: 'capital',
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
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Basel Capital', href: '/basel' },
          { label: 'RWA Breakdown' },
        ]}
        title="RWA Breakdown"
        subtitle="Risk-weighted assets by risk type · BoG CRD standardized approach"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      {needsBaseline ? (
        <div className="px-8 py-6">
          <EmptyState
            Icon={PlayCircle}
            title="Baseline run required"
            description="The RWA breakdown comes from a persisted baseline capital run. Run baseline to calculate and store the full risk-weighted asset detail for this reporting period."
            action={
              <div className="flex flex-col items-center gap-3">
                {runBaselineButton}
                {runBaseline.error && (
                  <ErrorPanel error={runBaseline.error} title="Run failed" />
                )}
              </div>
            }
          />
        </div>
      ) : (
        <QueryBoundary
          isLoading={breakdown.isLoading}
          error={breakdown.error}
          onRetry={() => breakdown.refetch()}
        >
          {data && (
            <div className="px-8 py-6 space-y-6">
              {/* Totals strip */}
              <div className="card px-5 py-4 grid grid-cols-2 md:grid-cols-4 gap-6">
                <TotalCell
                  label="Credit risk RWA"
                  value={num(data.creditRwaGhs)}
                />
                <TotalCell
                  label="Market risk RWA"
                  value={num(data.marketRwaGhs)}
                />
                <TotalCell
                  label="Operational risk RWA"
                  value={num(data.operationalRwaGhs)}
                />
                <TotalCell
                  label="Total RWA"
                  value={num(data.totalRwaGhs)}
                  emphasis
                />
              </div>

              <Card>
                <CardHeader
                  title="Credit risk"
                  subtitle="Standardized approach · BoG risk weights · CCF-converted off-balance lines and 0% RW lines shown for transparency"
                />
                <CardBody className="p-0">
                  <DataTable
                    columns={rwaColumns('Exposure class', 'Risk weight', 'RWA (GHS)')}
                    rows={[
                      ...creditRows,
                      {
                        item: 'TOTAL CREDIT RISK RWA',
                        exposureGHS: null,
                        weightPct: null,
                        rwaGHS: num(data.creditRwaGhs),
                        isTotal: true,
                      },
                    ]}
                    totalsRowMatcher={(r) => Boolean(r.isTotal)}
                  />
                </CardBody>
              </Card>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <Card>
                  <CardHeader
                    title="Market risk"
                    subtitle="Net open FX position · 8% capital charge · ×12.5 RWA multiplier"
                  />
                  <CardBody className="p-0">
                    <DataTable
                      columns={rwaColumns('Line', 'Charge rate', 'Amount (GHS)')}
                      rows={[
                        ...marketRows,
                        {
                          item: 'TOTAL MARKET RISK RWA',
                          exposureGHS: null,
                          weightPct: null,
                          rwaGHS: num(data.marketRwaGhs),
                          isTotal: true,
                        },
                      ]}
                      totalsRowMatcher={(r) => Boolean(r.isTotal)}
                    />
                  </CardBody>
                </Card>

                <Card>
                  <CardHeader
                    title="Operational risk"
                    subtitle="Basic Indicator Approach · alpha on 3-year average gross income"
                  />
                  <CardBody className="p-0">
                    <DataTable
                      columns={rwaColumns('Line', 'Alpha', 'Amount (GHS)')}
                      rows={[
                        ...operationalRows,
                        {
                          item: 'TOTAL OPERATIONAL RISK RWA',
                          exposureGHS: null,
                          weightPct: null,
                          rwaGHS: num(data.operationalRwaGhs),
                          isTotal: true,
                        },
                      ]}
                      totalsRowMatcher={(r) => Boolean(r.isTotal)}
                    />
                  </CardBody>
                </Card>
              </div>

              <p className="text-caption text-slate">
                Total RWA = Credit{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(num(data.creditRwaGhs), 'GHS')}
                </span>{' '}
                + Market{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(num(data.marketRwaGhs), 'GHS')}
                </span>{' '}
                + Operational{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(num(data.operationalRwaGhs), 'GHS')}
                </span>{' '}
                ={' '}
                <span className="font-mono font-medium text-navy">
                  {fmtCurrency(num(data.totalRwaGhs), 'GHS')}
                </span>
                . Source run{' '}
                <span className="font-mono text-navy">{shortId(data.runId)}</span>.
              </p>
            </div>
          )}
        </QueryBoundary>
      )}
    </>
  );
}

function TotalCell({
  label,
  value,
  emphasis = false,
}: {
  label: string;
  value: number;
  emphasis?: boolean;
}) {
  return (
    <div>
      <p className="text-micro font-medium uppercase tracking-wider text-slate">
        {label}
      </p>
      <p
        className={`mt-1 font-mono text-h1 tabular-nums ${
          emphasis ? 'text-navy font-semibold' : 'text-navy'
        }`}
      >
        {fmtCurrency(value, 'GHS')}
      </p>
    </div>
  );
}
