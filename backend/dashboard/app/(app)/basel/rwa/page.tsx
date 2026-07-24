'use client';

import { Loader2, PlayCircle } from 'lucide-react';
import type { CapitalLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import ChartFrame from '@/components/ui/ChartFrame';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import DonutChart from '@/components/charts/DonutChart';
import RwaBucketChart from '@/components/basel/charts/RwaBucketChart';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useCreateRegulatoryRun,
  useRwaBreakdown,
} from '@/lib/api/hooks';
import { fmtDateUTC, num, shortId } from '@/lib/api/values';
import { seriesColor } from '@/lib/chartTheme';
import { fmtCurrency, regShort } from '@/lib/format';

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
        r.exposureGHS === null ? '—' : fmtCurrency(r.exposureGHS),
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
      render: (r) => fmtCurrency(r.rwaGHS),
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

  const totalRwa = num(data?.totalRwaGhs);
  const creditRwa = num(data?.creditRwaGhs);
  const marketRwa = num(data?.marketRwaGhs);
  const operationalRwa = num(data?.operationalRwaGhs);
  const share = (v: number) =>
    totalRwa > 0 ? `${((v / totalRwa) * 100).toFixed(1)}% of total` : undefined;

  const bucketData = creditRows
    .filter((r) => r.rwaGHS > 0)
    .sort((a, b) => b.rwaGHS - a.rwaGHS)
    .map((r) => ({
      name: r.item,
      rwa: r.rwaGHS,
      exposure: r.exposureGHS,
      weightPct: r.weightPct,
    }));

  const splitSlices = data
    ? [
        { name: 'Credit risk', value: creditRwa, color: seriesColor(0) },
        { name: 'Operational risk', value: operationalRwa, color: seriesColor(1) },
        { name: 'Market risk', value: marketRwa, color: seriesColor(2) },
      ]
    : [];

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
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
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
          { label: 'RWA' },
        ]}
        title="RWA Breakdown"
        subtitle={`Risk-weighted assets by risk type · ${regShort()} CRD standardized approach`}
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
              {/* Totals */}
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <KpiStat
                  label="Credit risk RWA"
                  value={fmtCurrency(creditRwa)}
                  hint={share(creditRwa)}
                />
                <KpiStat
                  label="Market risk RWA"
                  value={fmtCurrency(marketRwa)}
                  hint={share(marketRwa)}
                />
                <KpiStat
                  label="Operational risk RWA"
                  value={fmtCurrency(operationalRwa)}
                  hint={share(operationalRwa)}
                />
                <KpiStat
                  label="Total RWA"
                  value={fmtCurrency(totalRwa)}
                  hint="Credit + market + operational"
                />
              </div>

              {/* Bucket bars + risk-type split */}
              <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <ChartFrame
                  className="lg:col-span-2"
                  title="Credit RWA by exposure class"
                  subtitle="Standardized approach — exposure × risk weight, largest first"
                  height={Math.max(200, bucketData.length * 34 + 40)}
                  footer={
                    <span>
                      Zero-RWA classes (0% risk weight) are listed in the
                      detail table below for transparency.
                    </span>
                  }
                >
                  <RwaBucketChart data={bucketData} />
                </ChartFrame>

                <SectionCard
                  title="Credit vs operational split"
                  subtitle={`Total ${fmtCurrency(totalRwa)}`}
                >
                  <div className="space-y-4">
                    <DonutChart
                      data={splitSlices}
                      centerLabel="Total RWA"
                      centerValue={fmtCurrency(totalRwa)}
                      format="ccy-m"
                    />
                    <ul className="space-y-2 text-caption pt-2 border-t border-border-light">
                      {splitSlices.map((s) => (
                        <li key={s.name} className="flex items-center gap-3">
                          <span
                            className="w-2 h-2 rounded-sm shrink-0"
                            style={{ background: s.color }}
                            aria-hidden
                          />
                          <span className="text-navy/85 flex-1">{s.name}</span>
                          <span className="font-mono text-navy tnum">
                            {fmtCurrency(s.value)}
                          </span>
                          <span className="font-mono text-slate w-12 text-right tnum">
                            {totalRwa > 0
                              ? `${((s.value / totalRwa) * 100).toFixed(1)}%`
                              : '—'}
                          </span>
                        </li>
                      ))}
                    </ul>
                  </div>
                </SectionCard>
              </div>

              <SectionCard
                title="Credit risk"
                subtitle={`Standardized approach · ${regShort()} risk weights · CCF-converted off-balance lines and 0% RW lines shown for transparency`}
                noPadding
                footer={
                  <span>
                    Source run{' '}
                    <span className="font-mono text-navy">
                      {shortId(data.runId)}
                    </span>
                  </span>
                }
              >
                <DataTable
                  columns={rwaColumns('Exposure class', 'Risk weight', 'RWA (GHS)')}
                  rows={[
                    ...creditRows,
                    {
                      item: 'TOTAL CREDIT RISK RWA',
                      exposureGHS: null,
                      weightPct: null,
                      rwaGHS: creditRwa,
                      isTotal: true,
                    },
                  ]}
                  totalsRowMatcher={(r) => Boolean(r.isTotal)}
                />
              </SectionCard>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <SectionCard
                  title="Market risk"
                  subtitle="Net open FX position · 8% capital charge · ×12.5 RWA multiplier"
                  noPadding
                >
                  <DataTable
                    columns={rwaColumns('Line', 'Charge rate', 'Amount (GHS)')}
                    rows={[
                      ...marketRows,
                      {
                        item: 'TOTAL MARKET RISK RWA',
                        exposureGHS: null,
                        weightPct: null,
                        rwaGHS: marketRwa,
                        isTotal: true,
                      },
                    ]}
                    totalsRowMatcher={(r) => Boolean(r.isTotal)}
                  />
                </SectionCard>

                <SectionCard
                  title="Operational risk"
                  subtitle="Basic Indicator Approach · alpha on 3-year average gross income"
                  noPadding
                >
                  <DataTable
                    columns={rwaColumns('Line', 'Alpha', 'Amount (GHS)')}
                    rows={[
                      ...operationalRows,
                      {
                        item: 'TOTAL OPERATIONAL RISK RWA',
                        exposureGHS: null,
                        weightPct: null,
                        rwaGHS: operationalRwa,
                        isTotal: true,
                      },
                    ]}
                    totalsRowMatcher={(r) => Boolean(r.isTotal)}
                  />
                </SectionCard>
              </div>

              <p className="text-caption text-slate">
                Total RWA = Credit{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(creditRwa)}
                </span>{' '}
                + Market{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(marketRwa)}
                </span>{' '}
                + Operational{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(operationalRwa)}
                </span>{' '}
                ={' '}
                <span className="font-mono font-medium text-navy">
                  {fmtCurrency(totalRwa)}
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
