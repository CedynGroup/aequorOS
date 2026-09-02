'use client';

/**
 * Vintages: cumulative delinquency by origination cohort — how each lending
 * season is maturing. Sparse history renders sparse; loans without an
 * origination date are excluded with the coverage disclosed.
 */

import type { Column } from '@/components/ui/DataTable';
import type { VintageCohortRead } from '@aequoros/risk-service-api';
import CreditWorkspace from '@/components/credit/CreditWorkspace';
import VintageCurvesChart from '@/components/credit/charts/VintageCurvesChart';
import DataTable from '@/components/ui/DataTable';
import QueryBoundary from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import { useBankContext } from '@/components/shell/BankContext';
import { useCreditVintages } from '@/lib/api/hooks';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtInt } from '@/lib/format';

function parAt(cohort: VintageCohortRead, age: number): string {
  const point = cohort.points.find((p) => p.monthsOnBook === age);
  return point ? `${num(point.par30Pct).toFixed(1)}%` : '—';
}

const columns: Column<VintageCohortRead>[] = [
  {
    key: 'cohort',
    header: 'Cohort',
    render: (r) => <span className="font-mono text-caption text-navy">{r.cohort}</span>,
  },
  {
    key: 'loans',
    header: 'Loans',
    align: 'right',
    numeric: true,
    render: (r) => fmtInt(r.initialLoanCount),
  },
  {
    key: 'initial',
    header: 'Initial exposure',
    align: 'right',
    numeric: true,
    render: (r) => fmtCurrency(num(r.initialExposureGhs)),
  },
  { key: 'm3', header: 'PAR30+ @ 3m', align: 'right', numeric: true, render: (r) => parAt(r, 3) },
  { key: 'm6', header: '@ 6m', align: 'right', numeric: true, render: (r) => parAt(r, 6) },
  { key: 'm9', header: '@ 9m', align: 'right', numeric: true, render: (r) => parAt(r, 9) },
  { key: 'm12', header: '@ 12m', align: 'right', numeric: true, render: (r) => parAt(r, 12) },
  {
    key: 'latest',
    header: 'Latest observed',
    align: 'right',
    numeric: true,
    render: (r) => {
      const last = r.points[r.points.length - 1];
      return last ? `${num(last.par30Pct).toFixed(1)}% @ ${last.monthsOnBook}m` : '—';
    },
  },
];

export default function CreditVintagesPage() {
  const { bank } = useBankContext();
  const vintages = useCreditVintages(bank?.id);

  return (
    <CreditWorkspace
      crumb="Vintages"
      subtitle="Cumulative delinquency by origination cohort — how each lending season is maturing."
    >
      {() => (
        <QueryBoundary
          isLoading={vintages.isLoading}
          error={vintages.error}
          onRetry={() => vintages.refetch()}
        >
          {vintages.data ? (
            vintages.data.available ? (
              <>
                <SectionCard
                  title="Cohort curves"
                  subtitle={`PAR 30+ share by months on book · ${fmtInt(vintages.data.monthsObserved ?? 0)} month-end books observed`}
                  footer={
                    vintages.data.originationCoveragePct != null &&
                    num(vintages.data.originationCoveragePct) < 100
                      ? `Cohorts cover ${num(vintages.data.originationCoveragePct).toFixed(1)}% of the book; loans without an origination date are excluded, never grouped.`
                      : undefined
                  }
                >
                  <VintageCurvesChart cohorts={vintages.data.cohorts ?? []} />
                </SectionCard>
                <SectionCard title="Cohort table" noPadding>
                  <DataTable
                    columns={columns}
                    rows={vintages.data.cohorts ?? []}
                    density="compact"
                    stickyHeader
                    maxHeight="50vh"
                  />
                </SectionCard>
              </>
            ) : (
              <SectionCard title="Cohort curves">
                <p className="text-body text-slate leading-relaxed">{vintages.data.reason}</p>
              </SectionCard>
            )
          ) : null}
        </QueryBoundary>
      )}
    </CreditWorkspace>
  );
}
