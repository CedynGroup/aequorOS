'use client';

import type { LiquidityDashboardLineRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RatioGauge from '@/components/ui/RatioGauge';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useLiquidityDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { num, statusTone } from '@/lib/api/values';
import { centralBankName, fmtCurrency } from '@/lib/format';

type WeightedRow = {
  item: string;
  balanceGHS: number;
  factor: number | null;
  weightedGHS: number;
  isTotal?: boolean;
};

function toRow(line: LiquidityDashboardLineRead): WeightedRow {
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
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const dashboard = useLiquidityDashboard(bankId);

  const data = dashboard.data;
  const nsfrMin = 100;
  const nsfrRedFloor = nsfrMin;

  const asfRows = (data?.asf ?? []).map(toRow);
  const rsfRows = (data?.rsf ?? []).map(toRow);
  const asfTotal = num(data?.metrics.asfTotalGhs);
  const rsfTotal = num(data?.metrics.rsfTotalGhs);
  const surplus = num(data?.metrics.asfTotalGhs) - num(data?.metrics.rsfTotalGhs);

  const computedAt = data?.live?.computedAt;


  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'NSFR' },
        ]}
        title="Net Stable Funding Ratio"
        subtitle={`Basel III NSFR · 1-year stable funding horizon · ${centralBankName()} has issued no NSFR requirement, so the Basel standard applies`}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => {
          void dashboard.refetch();
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
                value={fmtCurrency(num(data.metrics.asfTotalGhs))}
                hint="Liability-side weighting"
              />
              <KpiStat
                label="Required stable funding"
                value={fmtCurrency(num(data.metrics.rsfTotalGhs))}
                hint={`Funding surplus ${fmtCurrency(surplus)}`}
                status={surplus >= 0 ? 'ok' : 'crit'}
              />
            </div>

            <SectionCard
              title="Regulatory floor"
              subtitle="NSFR is a floor limit — compliant while the ratio stays above the Basel minimum"
              computedAt={computedAt}
            >
              <LimitBar
                label="NSFR"
                value={num(data.metrics.nsfrPct)}
                limit={nsfrRedFloor}
                warnAt={nsfrMin}
                direction="above"
                unit="%"
                limitLabel={nsfrRedFloor === nsfrMin ? 'Basel minimum' : 'Red floor'}
                warnLabel="Basel minimum"
                format={(v) => v.toFixed(1)}
              />
            </SectionCard>

            {asfRows.length === 0 && rsfRows.length === 0 ? (
              <EmptyState
                title="No live NSFR line detail"
                description="Current canonical data does not yet produce ASF or RSF detail. Refresh after the relevant funding data lands."
              />
            ) : (
              <>
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  <SectionCard
                    title="Available Stable Funding (ASF)"
                    subtitle="Liability-side weighting per Basel III §50"
                    noPadding
                    computedAt={computedAt}
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
                    {fmtCurrency(asfTotal)}
                  </span>{' '}
                  / Total RSF{' '}
                  <span className="font-mono text-navy">
                    {fmtCurrency(rsfTotal)}
                  </span>{' '}
                  ={' '}
                  <span className="font-mono font-medium text-success">
                    {num(data.metrics.nsfrPct).toFixed(2)}%
                  </span>
                  . Basel minimum {nsfrMin.toFixed(0)}%.{' '}
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
