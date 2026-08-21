'use client';

import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import QueryBoundary from '@/components/ui/QueryBoundary';
import KpiStat from '@/components/ui/KpiStat';
import { fmtCurrency, fmtPct } from '@/lib/format';
import { num } from '@/lib/api/values';
import { SdiExposureConcentrationChart } from './charts/SdiCapitalReviewCharts';
import { type SdiExposure, useSdiLargeExposures } from './sdiHooks';

const columns: Column<SdiExposure>[] = [
  { key: 'counterparty', header: 'Counterparty / group', render: (row) => row.counterparty_name },
  { key: 'connection', header: 'Basis', render: (row) => row.connection === 'group' ? 'Connected group' : 'Single counterparty' },
  { key: 'exposure', header: 'Exposure', numeric: true, render: (row) => fmtCurrency(num(row.exposure_ghs)) },
  { key: 'pct', header: '% Net Own Funds', numeric: true, render: (row) => row.pct_net_own_funds === null ? '—' : fmtPct(num(row.pct_net_own_funds), 2) },
  { key: 'limit', header: 'Applicable limit', numeric: true, render: (row) => fmtPct(num(row.large_exposure_limit_pct), 2) },
  { key: 'status', header: 'Status', render: (row) => <StatusPill tone={row.status === 'above_limit' ? 'critical' : row.status === 'not_computable' ? 'amber' : 'success'}>{row.exempt ? 'Exempt' : row.status === 'above_limit' ? 'Above limit' : row.status === 'not_computable' ? 'Not computable' : 'Within limit'}</StatusPill> },
];

export default function SdiExposureView({ bankId }: { bankId: string | undefined }) {
  const exposures = useSdiLargeExposures(bankId);
  const data = exposures.data;
  const breached = data?.exposures.filter((row) => row.status === 'above_limit').length ?? 0;
  const concentrationRows = (data?.exposures ?? [])
    .filter((row) => !row.exempt && row.pct_net_own_funds !== null)
    .sort((left, right) => num(right.pct_net_own_funds ?? '0') - num(left.pct_net_own_funds ?? '0'))
    .slice(0, 8)
    .map((row) => ({
      name: row.counterparty_name,
      pctNetOwnFunds: num(row.pct_net_own_funds ?? '0'),
      limitPct: num(row.connection === 'group' ? row.large_exposure_limit_pct : row.single_obligor_limit_pct),
      aboveLimit: row.status === 'above_limit',
    }));
  return (
    <div className="space-y-6 p-6">
      <PageHeader title="Large Exposures" subtitle="Connected-counterparty exposures against the specialised deposit-taking limits." />
      <QueryBoundary isLoading={exposures.isLoading} error={exposures.error} onRetry={() => exposures.refetch()}>
        {data ? (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
              <KpiStat label="Net Own Funds" value={fmtCurrency(num(data.net_own_funds_ghs))} status="ok" />
              <KpiStat label="Exposures above limit" value={String(breached)} status={breached > 0 ? 'crit' : 'ok'} />
              <KpiStat label="Source as of" value={data.as_of} status="ok" />
            </div>
            {concentrationRows.length > 0 ? (
              <SectionCard title="Largest exposure concentrations" subtitle="Top non-exempt counterparties and connected groups as a share of Net Own Funds, beside their applicable limit.">
                <SdiExposureConcentrationChart data={concentrationRows} />
              </SectionCard>
            ) : null}
            <SectionCard title="Counterparty and connected-group exposures" subtitle="Limits are resolved by institution class and reported against Net Own Funds." noPadding>
              <DataTable columns={columns} rows={data.exposures} density="compact" />
            </SectionCard>
            {data.findings.length > 0 && <SectionCard title="Data-quality findings"><ul className="list-disc pl-5 space-y-1 text-caption text-slate">{data.findings.map((finding) => <li key={finding}>{finding}</li>)}</ul></SectionCard>}
          </>
        ) : null}
      </QueryBoundary>
    </div>
  );
}