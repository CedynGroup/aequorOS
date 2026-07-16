'use client';

/**
 * Regulatory Submissions — Bank of Ghana return previews (BSD-2, BSD-3)
 * generated from persisted regulatory runs, plus the recent-runs audit trail
 * with full provenance (input hash + RunBadge). The return previews
 * themselves live on the owning module pages; this console links to them.
 */

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { ChevronRight, FileCheck2, FileBarChart2 } from 'lucide-react';
import type { RegulatoryRunSummaryRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import CopyButton from '@/components/ui/CopyButton';
import RunBadge from '@/components/ui/RunBadge';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  MODULE_HREFS,
  MODULE_LABELS,
} from '@/components/reports/hooks';
import {
  isNoBaselineRunError,
  useBsd2Preview,
  useBsd3Preview,
  useRegulatoryRuns,
} from '@/lib/api/hooks';
import { fmtDateUTC, labelize, shortId } from '@/lib/api/values';

export default function SubmissionsPage() {
  const router = useRouter();
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const bsd3 = useBsd3Preview(bankId, periodId);
  const bsd2 = useBsd2Preview(bankId, periodId);
  const runsQuery = useRegulatoryRuns(bankId, { limit: 10 });
  const runs = runsQuery.data?.runs ?? [];

  const returns: {
    form: string;
    title: string;
    description: string;
    href: string;
    query: { isLoading: boolean; error: unknown };
    runId?: string;
    validations?: { total: number; failed: number };
  }[] = [
    {
      form: 'BSD-3',
      title: 'BoG Liquidity Return (LCR & NSFR)',
      description:
        'Liquidity Coverage Ratio and Net Stable Funding Ratio return, generated from the latest successful baseline liquidity run.',
      href: '/liquidity/submission',
      query: bsd3,
      runId: bsd3.data?.runId,
      validations: bsd3.data
        ? {
            total: bsd3.data.validations.length,
            failed: bsd3.data.validations.filter((v) => !v.passed).length,
          }
        : undefined,
    },
    {
      form: 'BSD-2',
      title: 'BoG Capital Adequacy Return',
      description:
        'Capital structure, risk-weighted assets, and capital ratios return, generated from the latest successful baseline capital run.',
      href: '/basel/submissions',
      query: bsd2,
      runId: bsd2.data?.runId,
      validations: bsd2.data
        ? {
            total: bsd2.data.validations.length,
            failed: bsd2.data.validations.filter((v) => !v.passed).length,
          }
        : undefined,
    },
  ];

  const columns: Column<RegulatoryRunSummaryRead>[] = [
    {
      key: 'module',
      header: 'Module',
      render: (run) => (
        <span className="font-medium text-navy whitespace-nowrap">
          {run.module ? MODULE_LABELS[run.module] ?? labelize(run.module) : '—'}
        </span>
      ),
    },
    {
      key: 'scenario',
      header: 'Scenario',
      render: (run) => (
        <span className="text-navy/85">{labelize(run.scenarioCode)}</span>
      ),
    },
    {
      key: 'period',
      header: 'Period',
      render: (run) => (
        <span className="font-mono text-caption text-slate">
          {run.periodLabel}
        </span>
      ),
    },
    {
      key: 'hash',
      header: 'Input hash',
      render: (run) => (
        <span
          className="inline-flex items-center gap-1.5"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <span className="font-mono text-caption text-slate">
            {shortId(run.inputHash, 10)}
          </span>
          <CopyButton text={run.inputHash} label="input hash" />
        </span>
      ),
    },
    {
      key: 'provenance',
      header: 'Provenance',
      render: (run) => <RunBadge run={run} />,
    },
    {
      key: 'status',
      header: 'Status',
      align: 'right',
      render: (run) => (
        <StatusPill tone={run.status === 'succeeded' ? 'success' : 'critical'}>
          {labelize(run.status)}
        </StatusPill>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Regulatory Submissions"
        subtitle="Bank of Ghana return previews · Generated from persisted regulatory runs"
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <div className="px-8 py-6 space-y-6">
        {/* Return availability for the selected period */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {returns.map((item) => {
            let tone: StatusTone = 'slate';
            let statusLabel = 'Checking…';
            let ready = false;
            if (!item.query.isLoading) {
              if (item.runId) {
                tone = 'success';
                statusLabel = 'Ready';
                ready = true;
              } else if (isNoBaselineRunError(item.query.error)) {
                tone = 'amber';
                statusLabel = 'Baseline run required';
              } else if (item.query.error) {
                tone = 'slate';
                statusLabel = 'Unavailable';
              }
            }
            return (
              <Card key={item.form}>
                <CardHeader
                  title={
                    <span className="inline-flex items-center gap-2">
                      <FileCheck2 size={15} className="text-action" aria-hidden />
                      {item.title}
                    </span>
                  }
                  subtitle={`Form ${item.form} · ${period?.label ?? ''}`}
                  action={<StatusPill tone={tone}>{statusLabel}</StatusPill>}
                />
                <CardBody>
                  <p className="text-body text-slate leading-relaxed">
                    {item.description}
                  </p>
                  <div className="mt-4 flex items-center justify-between gap-3 flex-wrap">
                    <Link
                      href={item.href}
                      className={`inline-flex items-center gap-1 text-caption font-medium ${
                        ready
                          ? 'text-action hover:text-action-hover'
                          : 'text-slate hover:text-navy'
                      }`}
                    >
                      {ready ? 'View preview' : 'Open module submission page'}
                      <ChevronRight size={12} aria-hidden />
                    </Link>
                    {item.runId && (
                      <span className="inline-flex items-center gap-3 text-[10px] text-slate font-mono tnum">
                        {item.validations && (
                          <span
                            className={
                              item.validations.failed > 0
                                ? 'text-critical'
                                : 'text-success'
                            }
                          >
                            {item.validations.failed > 0
                              ? `${item.validations.failed}/${item.validations.total} checks failed`
                              : `${item.validations.total} checks passed`}
                          </span>
                        )}
                        <span title={`Source run ${item.runId}`}>
                          run {shortId(item.runId, 8)}
                        </span>
                      </span>
                    )}
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>

        {/* Audit trail — recent regulatory runs */}
        <SectionCard
          title="Recent regulatory runs"
          subtitle="Audit trail of the persisted calculation runs behind every return"
          noPadding
          runBadge={runs[0] ? <RunBadge run={runs[0]} /> : undefined}
          footer={
            runs[0] ? (
              <span className="whitespace-nowrap">Latest run provenance</span>
            ) : undefined
          }
        >
          <QueryBoundary
            isLoading={runsQuery.isLoading}
            error={runsQuery.error}
            onRetry={() => runsQuery.refetch()}
            skeleton={<SkeletonTable rows={5} />}
          >
            {runs.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  Icon={FileBarChart2}
                  title="No regulatory runs yet"
                  description="Run a baseline from the Liquidity or Basel Capital module to create the first auditable run."
                />
              </div>
            ) : (
              <DataTable
                columns={columns}
                rows={runs}
                density="compact"
                onRowClick={(run) => {
                  const href = run.module ? MODULE_HREFS[run.module] : null;
                  if (href) router.push(href);
                }}
              />
            )}
          </QueryBoundary>
        </SectionCard>
      </div>
    </>
  );
}
