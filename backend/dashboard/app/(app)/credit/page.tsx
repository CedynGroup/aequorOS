'use client';

/**
 * Credit Overview: asset quality, provisioning and the prudential NPL position.
 *
 * Every figure comes from the credit service payload — the classification is
 * the platform's own class-aware grid (bank 5-grade / SDI 4-grade), the NPL
 * ceiling resolves from the governed parameter register, and absence renders as
 * absence ("—" / "Not assessed"), never as zero.
 */

import type { Column } from '@/components/ui/DataTable';
import type { CreditValidationRead, LoanGradeBucketRead } from '@aequoros/risk-service-api';
import CreditWorkspace from '@/components/credit/CreditWorkspace';
import DataTable from '@/components/ui/DataTable';
import KpiStat from '@/components/ui/KpiStat';
import LimitBar from '@/components/ui/LimitBar';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import ValidationList from '@/components/ui/ValidationList';
import {
  SdiDelinquencyChart,
  SdiLoanQualityChart,
} from '@/components/basel/charts/SdiCapitalReviewCharts';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtInt, regShort } from '@/lib/format';

const gradeColumns: Column<LoanGradeBucketRead>[] = [
  {
    key: 'grade',
    header: 'Grade',
    render: (row) => <span className="text-body text-navy">{labelize(row.grade)}</span>,
  },
  { key: 'count', header: 'Loans', align: 'right', numeric: true, render: (r) => fmtInt(r.count) },
  {
    key: 'exposure',
    header: 'Exposure',
    align: 'right',
    numeric: true,
    render: (r) => fmtCurrency(num(r.exposureGhs)),
  },
  {
    key: 'provision',
    header: 'Provision required',
    align: 'right',
    numeric: true,
    render: (r) => fmtCurrency(num(r.provisionRequiredGhs)),
  },
  {
    key: 'status',
    header: 'Status',
    align: 'right',
    render: (r) => (
      <StatusPill tone={r.nonPerforming ? 'critical' : 'success'}>
        {r.nonPerforming ? 'Non-performing' : 'Performing'}
      </StatusPill>
    ),
  },
];

function delinquencySeverity(code: string): 'current' | 'early' | 'npl' | 'loss' {
  if (code === 'current') return 'current';
  if (code === '1_29' || code === '30_59' || code === '60_89') return 'early';
  if (code === '360_plus') return 'loss';
  return 'npl';
}

function kpiStatus(status: string): 'ok' | 'warn' | 'crit' | undefined {
  if (status === 'red') return 'crit';
  if (status === 'amber') return 'warn';
  if (status === 'green') return 'ok';
  return undefined;
}

export default function CreditOverviewPage() {
  return (
    <CreditWorkspace
      crumb="Overview"
      subtitle="Asset quality, provisioning and early-warning position across the loan book."
    >
      {({ data, metrics }) => {
        const par = Object.fromEntries(data.portfolioAtRisk.map((m) => [m.code, m]));
        const par30 = 'par_30' in par ? par['par_30'] : undefined;
        const par90 = 'par_90' in par ? par['par_90'] : undefined;
        const nplPct = num(metrics.nplRatioPct);
        const isSdi = data.institutionClass === 'sdi';
        return (
          <>
            <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-4">
              <KpiStat
                label="Gross loan book"
                value={fmtCurrency(num(metrics.grossLoansGhs))}
                hint={`${fmtInt(metrics.loanCount)} loans`}
              />
              <KpiStat
                label="NPL ratio"
                value={`${nplPct.toFixed(2)}%`}
                status={kpiStatus(metrics.nplStatus ?? 'na')}
                hint={
                  metrics.nplLimitPct != null
                    ? `${regShort()} prudential limit ${num(metrics.nplLimitPct).toFixed(0)}%`
                    : 'Prudential limit not assessed'
                }
              />
              <KpiStat
                label="NPL exposure"
                value={fmtCurrency(num(metrics.nplExposureGhs))}
                status={kpiStatus(metrics.nplStatus ?? 'na')}
              />
              <KpiStat
                label="Provision required"
                value={fmtCurrency(num(metrics.totalProvisionRequiredGhs))}
                hint="Active classification grid"
              />
              <KpiStat
                label="Provision coverage"
                value={
                  metrics.provisionCoveragePct != null
                    ? `${num(metrics.provisionCoveragePct).toFixed(1)}%`
                    : metrics.provisionsHeld != null
                      ? 'Not applicable'
                      : '—'
                }
                status={
                  metrics.provisionCoveragePct != null && num(metrics.provisionCoveragePct) < 100
                    ? 'warn'
                    : undefined
                }
                hint={
                  metrics.provisionsHeld != null
                    ? 'Specific provisions held ÷ NPL exposure'
                    : 'No loan states a held provision; coverage is unavailable, not zero'
                }
              />
              <KpiStat
                label="PAR 30+"
                value={par30 ? `${(num(par30.ratio) * 100).toFixed(2)}%` : '—'}
                status={par30 && num(par30.ratio) > 0 ? 'warn' : undefined}
                hint="Raw DPD exposure ÷ gross loan book"
              />
              <KpiStat
                label="PAR 90+"
                value={par90 ? `${(num(par90.ratio) * 100).toFixed(2)}%` : '—'}
                status={par90 && num(par90.ratio) > 0 ? 'crit' : undefined}
                hint="Raw DPD exposure ÷ gross loan book"
              />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SectionCard
                title={`NPL ratio against the ${regShort()} limit`}
                subtitle="Notice on regulatory measures to reduce non-performing loans."
              >
                {metrics.nplLimitPct != null ? (
                  <LimitBar
                    label="NPL ratio"
                    value={nplPct}
                    limit={num(metrics.nplLimitPct)}
                    warnAt={num(metrics.nplLimitPct) * 0.8}
                    direction="below"
                    unit="%"
                  />
                ) : (
                  <p className="text-body text-slate">
                    The prudential NPL limit is not configured for this institution class, so the
                    ratio is reported without a compliance status.
                  </p>
                )}
                {metrics.nplRestrictionLevelPct != null ? (
                  <p className="mt-3 text-micro text-slate leading-relaxed">
                    At {num(metrics.nplRestrictionLevelPct).toFixed(0)}% and above, dividend,
                    bonus and loan-growth restrictions apply immediately.
                  </p>
                ) : null}
              </SectionCard>
              <SectionCard
                title="Loan quality and provision burden"
                subtitle="Exposure and required provision by the active classification grid."
              >
                <SdiLoanQualityChart
                  data={data.grades.map((bucket) => ({
                    grade: labelize(bucket.grade),
                    exposure: num(bucket.exposureGhs),
                    provision: num(bucket.provisionRequiredGhs),
                  }))}
                />
              </SectionCard>
            </div>

            <SectionCard
              title="Classification and provisioning"
              subtitle={
                isSdi
                  ? 'NBFI 4-grade classification: Standard, Sub-standard, Doubtful and Loss; non-performing at 90 days past due.'
                  : `${regShort()} 5-grade classification including OLEM; non-performing at 90 days past due.`
              }
              noPadding
            >
              <DataTable columns={gradeColumns} rows={data.grades} density="compact" />
            </SectionCard>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
              <SectionCard
                title="Delinquency distribution"
                subtitle="Raw days-past-due bands across loans with a stated DPD."
              >
                <SdiDelinquencyChart
                  data={data.delinquencyBuckets.map((bucket) => ({
                    label: bucket.label,
                    exposure: num(bucket.exposureGhs),
                    count: bucket.count,
                    severity: delinquencySeverity(bucket.code),
                  }))}
                />
              </SectionCard>
              <SectionCard title="Engine findings" subtitle="Validation rules from the credit engine." noPadding>
                <ValidationList
                  validations={data.validations.map((row: CreditValidationRead) => ({
                    ruleCode: row.ruleCode,
                    passed: row.passed,
                    severity: row.severity,
                    message: row.message,
                  }))}
                />
              </SectionCard>
            </div>

            {(metrics.unclassifiedCount > 0 ||
              metrics.stageProxyCount > 0 ||
              data.pendingParameters.length > 0) && (
              <SectionCard title="Data and parameter notes">
                <div className="space-y-1.5">
                  {metrics.unclassifiedCount > 0 && (
                    <p className="text-micro text-slate leading-relaxed">
                      {fmtInt(metrics.unclassifiedCount)} loan(s) carry neither a days-past-due
                      nor an IFRS 9 stage ({fmtCurrency(num(metrics.unclassifiedExposureGhs))} in
                      exposure); they are excluded from both the performing and the NPL legs —
                      never booked performing.
                    </p>
                  )}
                  {metrics.stageProxyCount > 0 && (
                    <p className="text-micro text-slate leading-relaxed">
                      {fmtInt(metrics.stageProxyCount)} loan(s) classified via the IFRS 9 stage
                      proxy (no stated days-past-due).
                    </p>
                  )}
                  {data.pendingParameters.length > 0 && (
                    <p className="text-micro text-slate leading-relaxed">
                      Parameters awaiting confirmation:{' '}
                      {data.pendingParameters.map((code) => labelize(code)).join(', ')}.
                    </p>
                  )}
                </div>
              </SectionCard>
            )}
          </>
        );
      }}
    </CreditWorkspace>
  );
}
