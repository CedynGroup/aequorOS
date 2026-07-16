'use client';

import { Loader2, PlayCircle } from 'lucide-react';
import type { RegulatoryLineItemRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import SectionCard from '@/components/ui/SectionCard';
import RunBadge from '@/components/ui/RunBadge';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { runComputedAt, runThresholds } from '@/components/liquidity/runData';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCreateRegulatoryRun,
  useLiquidityDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC, num, statusTone } from '@/lib/api/values';
import { fmtCurrency } from '@/lib/format';

type WeightedRow = {
  item: string;
  balanceGHS: number;
  factor: number | null;
  weightedGHS: number;
  isTotal?: boolean;
};

function toRow(line: RegulatoryLineItemRead): WeightedRow {
  return {
    item: line.description,
    balanceGHS: num(line.exposureAmount),
    factor: line.ratePct === null ? null : num(line.ratePct),
    weightedGHS: num(line.weightedAmount),
  };
}

function weightedColumns(
  categoryHeader: string,
  factorHeader: string,
  amountHeader: string
): Column<WeightedRow>[] {
  return [
    { key: 'item', header: categoryHeader, render: (r) => r.item, width: '50%' },
    {
      key: 'bal',
      header: 'Balance',
      numeric: true,
      render: (r) => (r.isTotal ? '—' : fmtCurrency(r.balanceGHS)),
    },
    {
      key: 'fct',
      header: factorHeader,
      numeric: true,
      render: (r) => (r.factor === null ? '—' : `${r.factor.toFixed(0)}%`),
    },
    {
      key: 'amt',
      header: amountHeader,
      numeric: true,
      render: (r) => fmtCurrency(r.weightedGHS),
    },
  ];
}

export default function NSFRDashboard() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useLiquidityDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runBaseline = useCreateRegulatoryRun(bankId);

  const data = dashboard.data;
  const run = latestRun.data;
  const thresholds = runThresholds(run);
  const nsfrMin = thresholds['nsfr_min'] ?? 100;
  const nsfrRedFloor = thresholds['nsfr_amber_floor'] ?? nsfrMin;

  const asfRows = (run?.lineItems ?? [])
    .filter((line) => line.section === 'asf')
    .map(toRow);
  const rsfRows = (run?.lineItems ?? [])
    .filter((line) => line.section === 'rsf')
    .map(toRow);
  const asfTotal = num(run?.metrics?.['asf_total_ghs']);
  const rsfTotal = num(run?.metrics?.['rsf_total_ghs']);
  const surplus = num(data?.metrics.asfTotalGhs) - num(data?.metrics.rsfTotalGhs);

  const computedAt = runComputedAt(run);

  const runBaselineButton = (
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
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'NSFR' },
        ]}
        title="Net Stable Funding Ratio"
        subtitle="Basel III NSFR per BoG CRD · 1-year stable funding horizon"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            {run && <RunBadge run={run} />}
            {runBaselineButton}
          </div>
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading || latestRun.isLoading}
        error={dashboard.error ?? latestRun.error}
        onRetry={() => {
          void dashboard.refetch();
          void latestRun.refetch();
        }}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
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
                value={fmtCurrency(num(data.metrics.asfTotalGhs), 'GHS')}
                hint="Liability-side weighting"
              />
              <KpiStat
                label="Required stable funding"
                value={fmtCurrency(num(data.metrics.rsfTotalGhs), 'GHS')}
                hint={`Funding surplus ${fmtCurrency(surplus, 'GHS')}`}
                status={surplus >= 0 ? 'ok' : 'crit'}
              />
            </div>

            <SectionCard
              title="Regulatory floor"
              subtitle="NSFR is a floor limit — compliant while the ratio stays above the BoG minimum"
              computedAt={computedAt}
              runBadge={run ? <RunBadge run={run} /> : undefined}
            >
              <LimitBar
                label="NSFR"
                value={num(data.metrics.nsfrPct)}
                limit={nsfrRedFloor}
                warnAt={nsfrMin}
                direction="above"
                unit="%"
                limitLabel={nsfrRedFloor === nsfrMin ? 'BoG minimum' : 'Red floor'}
                warnLabel="BoG minimum"
                format={(v) => v.toFixed(1)}
              />
            </SectionCard>

            {!run ? (
              <EmptyState
                title="No stored baseline run for this period"
                description="ASF and RSF line-item detail comes from a persisted baseline liquidity run. Run baseline to calculate and store the full NSFR breakdown."
                action={runBaselineButton}
              />
            ) : (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <SectionCard
                    title="Available Stable Funding (ASF)"
                    subtitle="Liability-side weighting per Basel III §50"
                    noPadding
                    computedAt={computedAt}
                    runBadge={<RunBadge run={run} />}
                  >
                    <DataTable
                      columns={weightedColumns(
                        'Liability category',
                        'ASF factor',
                        'ASF amount'
                      )}
                      rows={[
                        ...asfRows,
                        {
                          item: 'TOTAL ASF',
                          balanceGHS: 0,
                          factor: null,
                          weightedGHS: asfTotal,
                          isTotal: true,
                        },
                      ]}
                      totalsRowMatcher={(r) => Boolean(r.isTotal)}
                    />
                  </SectionCard>

                  <SectionCard
                    title="Required Stable Funding (RSF)"
                    subtitle="Asset-side weighting per Basel III §52"
                    noPadding
                    computedAt={computedAt}
                    runBadge={<RunBadge run={run} />}
                  >
                    <DataTable
                      columns={weightedColumns(
                        'Asset category',
                        'RSF factor',
                        'RSF amount'
                      )}
                      rows={[
                        ...rsfRows,
                        {
                          item: 'TOTAL RSF',
                          balanceGHS: 0,
                          factor: null,
                          weightedGHS: rsfTotal,
                          isTotal: true,
                        },
                      ]}
                      totalsRowMatcher={(r) => Boolean(r.isTotal)}
                    />
                  </SectionCard>
                </div>

                <p className="text-caption text-slate">
                  NSFR = Total ASF{' '}
                  <span className="font-mono text-navy">
                    {fmtCurrency(asfTotal, 'GHS')}
                  </span>{' '}
                  / Total RSF{' '}
                  <span className="font-mono text-navy">
                    {fmtCurrency(rsfTotal, 'GHS')}
                  </span>{' '}
                  ={' '}
                  <span className="font-mono font-medium text-success">
                    {num(data.metrics.nsfrPct).toFixed(2)}%
                  </span>
                  . BoG minimum {nsfrMin.toFixed(0)}%.{' '}
                  {bank?.name ?? 'The bank'} holds{' '}
                  <span className="font-mono text-navy">
                    {(num(data.metrics.nsfrPct) - nsfrMin).toFixed(2)} pts
                  </span>{' '}
                  of headroom.
                </p>
              </>
            )}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
