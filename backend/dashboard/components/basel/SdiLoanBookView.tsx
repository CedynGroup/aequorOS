'use client';

import PageHeader from '@/components/ui/PageHeader';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import QueryBoundary from '@/components/ui/QueryBoundary';
import KpiStat from '@/components/ui/KpiStat';
import { fmtCurrency } from '@/lib/format';
import { num } from '@/lib/api/values';
import { SdiDelinquencyChart, SdiLoanQualityChart } from './charts/SdiCapitalReviewCharts';
import { type DelinquencyBucket, type LoanGradeBucket, useLoanClassification } from './sdiHooks';

const columns: Column<LoanGradeBucket>[] = [
  { key: 'grade', header: 'Grade', render: (row) => <span className="capitalize text-navy">{row.grade}</span> },
  { key: 'count', header: 'Loans', numeric: true, render: (row) => String(row.count) },
  { key: 'exposure', header: 'Exposure', numeric: true, render: (row) => fmtCurrency(num(row.exposure_ghs)) },
  {
    key: 'provision',
    header: 'Provision required',
    numeric: true,
    render: (row) => fmtCurrency(num(row.provision_required_ghs)),
  },
  {
    key: 'npl',
    header: 'Status',
    render: (row) => row.non_performing ? <StatusPill tone="critical">NPL</StatusPill> : <StatusPill tone="success">Performing</StatusPill>,
  },
];

const delinquencyColumns: Column<DelinquencyBucket>[] = [
  { key: 'label', header: 'Days past due', render: (row) => row.label },
  { key: 'count', header: 'Loans', numeric: true, render: (row) => String(row.count) },
  {
    key: 'exposure',
    header: 'Exposure',
    numeric: true,
    render: (row) => fmtCurrency(num(row.exposure_ghs)),
  },
];

export default function SdiLoanBookView({ bankId }: { bankId: string | undefined }) {
  const classification = useLoanClassification(bankId);
  const data = classification.data;
  const isSdi = data?.institution_class === 'sdi';
  const portfolioAtRisk = (code: string) =>
    data?.portfolio_at_risk.find((metric) => metric.code === code) ?? null;
  const par30 = portfolioAtRisk('par_30');
  const par90 = portfolioAtRisk('par_90');
  return (
    <div className="space-y-6 p-6">
      <PageHeader
        title="Loan Book"
        subtitle={isSdi
          ? 'NBFI loan classification and provisioning for a specialised deposit-taking institution.'
          : 'Loan classification, delinquency, and provisioning across the banking book.'}
      />
      <QueryBoundary
        isLoading={classification.isLoading}
        error={classification.error}
        onRetry={() => classification.refetch()}
      >
        {data ? (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-4">
              <KpiStat label="Loans assessed" value={String(data.loan_count)} status="ok" />
              <KpiStat label="NPL ratio" value={`${(num(data.npl_ratio) * 100).toFixed(2)}%`} status={num(data.npl_ratio) > 0 ? 'warn' : 'ok'} />
              <KpiStat label="NPL exposure" value={fmtCurrency(num(data.npl_exposure_ghs))} status={num(data.npl_exposure_ghs) > 0 ? 'warn' : 'ok'} />
              <KpiStat label="PAR 30+" value={par30 ? `${(num(par30.ratio) * 100).toFixed(2)}%` : '—'} status={par30 && num(par30.ratio) > 0 ? 'warn' : 'ok'} hint="Raw DPD exposure ÷ gross loan book" />
              <KpiStat label="PAR 90+" value={par90 ? `${(num(par90.ratio) * 100).toFixed(2)}%` : '—'} status={par90 && num(par90.ratio) > 0 ? 'crit' : 'ok'} hint="Raw DPD exposure ÷ gross loan book" />
              <KpiStat label="Provision required" value={fmtCurrency(num(data.total_provision_required_ghs))} status="ok" />
              <KpiStat
                label="Provision coverage"
                value={
                  data.provision_coverage_pct !== null
                    ? `${num(data.provision_coverage_pct).toFixed(1)}%`
                    : data.provisions_held !== null
                      ? 'Not applicable'
                      : '—'
                }
                status={
                  data.provision_coverage_pct !== null && num(data.provision_coverage_pct) < 100
                    ? 'warn'
                    : 'ok'
                }
                hint={
                  data.provisions_held !== null
                    ? `Specific provisions held ÷ NPL exposure · stated on ${data.provisions_held.stated_loan_count} loans`
                    : 'No loan states a held provision; coverage is unavailable, not zero'
                }
              />
            </div>
            <SectionCard title="Classification and provisioning" subtitle={isSdi ? 'NBFI 4-grade classification: Standard, Sub-standard, Doubtful, and Loss; non-performing at 90 days past due.' : 'BoG 5-grade classification including OLEM; non-performing at 90 days past due.'} noPadding>
              <DataTable columns={columns} rows={data.buckets} density="compact" />
            </SectionCard>
            <SectionCard title="Loan quality and provision burden" subtitle="Exposure and required provision by the active institution-class classification grid.">
              <SdiLoanQualityChart
                data={data.buckets.map((bucket) => ({
                  grade: bucket.grade.replace(/(^|\s)\S/g, (letter) => letter.toUpperCase()),
                  exposure: num(bucket.exposure_ghs),
                  provision: num(bucket.provision_required_ghs),
                }))}
              />
            </SectionCard>
            <div className="grid gap-6 xl:grid-cols-2">
              <SectionCard title="Raw delinquency distribution" subtitle="Exact days-past-due exposure. IFRS 9 stage-proxy loans are excluded from these buckets.">
                <SdiDelinquencyChart
                  data={data.delinquency_buckets.map((bucket) => ({
                    label: bucket.label,
                    exposure: num(bucket.exposure_ghs),
                    count: bucket.count,
                    severity: bucket.code === '360_plus' ? 'loss' : bucket.code === '90_179' || bucket.code === '180_359' ? 'npl' : bucket.code === '30_59' || bucket.code === '60_89' ? 'early' : 'current',
                  }))}
                />
              </SectionCard>
              <SectionCard title="Portfolio at risk" subtitle="Raw DPD exposure at or beyond each threshold, as a share of the gross loan portfolio." noPadding>
                <DataTable
                  columns={[
                    { key: 'label', header: 'Metric', render: (row) => row.label },
                    { key: 'exposure', header: 'Exposure', numeric: true, render: (row) => fmtCurrency(num(row.exposure_ghs)) },
                    { key: 'ratio', header: 'Gross portfolio', numeric: true, render: (row) => `${(num(row.ratio) * 100).toFixed(2)}%` },
                  ]}
                  rows={data.portfolio_at_risk}
                  density="compact"
                />
              </SectionCard>
            </div>
            <SectionCard title="Raw DPD buckets" subtitle="Exact delinquency bands used for portfolio monitoring; these do not replace the active regulatory classification." noPadding>
              <DataTable columns={delinquencyColumns} rows={data.delinquency_buckets} density="compact" />
            </SectionCard>
            {(data.stage_proxy_count > 0 || data.unclassified_count > 0 || data.pending_parameters.length > 0 || data.dpd_covered_count < data.loan_count) && (
              <SectionCard title="Data and parameter notes">
                <div className="space-y-2 text-caption text-slate">
                  {data.dpd_covered_count < data.loan_count && <p>{data.dpd_covered_count} of {data.loan_count} loan(s) have stated raw days-past-due and are included in exact delinquency measures.</p>}
                  {data.stage_proxy_count > 0 && <p>{data.stage_proxy_count} loan(s) use IFRS 9 stage as a days-past-due proxy.</p>}
                  {data.unclassified_count > 0 && <p>{data.unclassified_count} loan(s) are unclassified because no days-past-due or IFRS 9 stage was supplied.</p>}
                  {data.pending_parameters.length > 0 && <p>Parameters awaiting confirmation: {data.pending_parameters.join(', ')}.</p>}
                </div>
              </SectionCard>
            )}
          </>
        ) : null}
      </QueryBoundary>
    </div>
  );
}