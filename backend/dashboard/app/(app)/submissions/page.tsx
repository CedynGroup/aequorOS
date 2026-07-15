'use client';

import Link from 'next/link';
import { ChevronRight, FileCheck2 } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import { Card, CardBody, CardHeader } from '@/components/ui/Card';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isNoBaselineRunError,
  useBsd2Preview,
  useBsd3Preview,
  useRegulatoryRuns,
} from '@/lib/api/hooks';
import { fmtDateUTC, fmtTimestamp, labelize, shortId } from '@/lib/api/values';

export default function SubmissionsPage() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const bsd3 = useBsd3Preview(bankId, periodId);
  const bsd2 = useBsd2Preview(bankId, periodId);
  const runsQuery = useRegulatoryRuns(bankId, { limit: 10 });

  const returns: {
    form: string;
    title: string;
    description: string;
    href: string;
    query: { isLoading: boolean; error: unknown; data: unknown };
  }[] = [
    {
      form: 'BSD-3',
      title: 'BoG Liquidity Return (LCR & NSFR)',
      description:
        'Liquidity Coverage Ratio and Net Stable Funding Ratio return, generated from the latest successful baseline liquidity run.',
      href: '/liquidity/submission',
      query: bsd3,
    },
    {
      form: 'BSD-2',
      title: 'BoG Capital Adequacy Return',
      description:
        'Capital structure, risk-weighted assets, and capital ratios return, generated from the latest successful baseline capital run.',
      href: '/basel/submissions',
      query: bsd2,
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
              if (item.query.data) {
                tone = 'success';
                statusLabel = 'Ready — view preview';
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
                  <div className="mt-4">
                    {ready ? (
                      <Link
                        href={item.href}
                        className="inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
                      >
                        View preview <ChevronRight size={12} aria-hidden />
                      </Link>
                    ) : (
                      <Link
                        href={item.href}
                        className="inline-flex items-center gap-1 text-caption font-medium text-slate hover:text-navy"
                      >
                        Open module submission page{' '}
                        <ChevronRight size={12} aria-hidden />
                      </Link>
                    )}
                  </div>
                </CardBody>
              </Card>
            );
          })}
        </div>

        {/* Audit trail — recent regulatory runs */}
        <Card>
          <CardHeader
            title="Recent regulatory runs"
            subtitle="Audit trail of the persisted calculation runs behind every return"
          />
          <CardBody className="p-0">
            <QueryBoundary
              isLoading={runsQuery.isLoading}
              error={runsQuery.error}
              onRetry={() => runsQuery.refetch()}
              skeleton={
                <p className="px-5 py-4 text-body text-slate">Loading runs…</p>
              }
            >
              {(runsQuery.data?.runs ?? []).length === 0 ? (
                <p className="px-5 py-4 text-body text-slate">
                  No regulatory runs yet — run a baseline from the Liquidity or
                  Basel Capital module to create the first auditable run.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-body border-collapse">
                    <thead>
                      <tr className="border-b border-border bg-surface text-micro font-medium uppercase tracking-wider text-slate">
                        <th className="text-left px-5 py-2.5">Created</th>
                        <th className="text-left px-5 py-2.5">Module</th>
                        <th className="text-left px-5 py-2.5">Scenario</th>
                        <th className="text-left px-5 py-2.5">Period</th>
                        <th className="text-left px-5 py-2.5">Engine</th>
                        <th className="text-left px-5 py-2.5">Input hash</th>
                        <th className="text-right px-5 py-2.5">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(runsQuery.data?.runs ?? []).map((run) => (
                        <tr
                          key={run.id}
                          className="border-b border-border-light last:border-b-0 hover:bg-surface-alt"
                        >
                          <td className="px-5 py-3 font-mono text-caption text-slate whitespace-nowrap">
                            {fmtTimestamp(run.createdAt)}
                          </td>
                          <td className="px-5 py-3 font-medium text-navy">
                            {run.module ? labelize(run.module) : '—'}
                          </td>
                          <td className="px-5 py-3 text-navy/85">
                            {labelize(run.scenarioCode)}
                          </td>
                          <td className="px-5 py-3 font-mono text-caption text-slate">
                            {run.periodLabel}
                          </td>
                          <td className="px-5 py-3 font-mono text-caption text-slate">
                            {run.engineVersion}
                          </td>
                          <td className="px-5 py-3 font-mono text-caption text-slate">
                            {shortId(run.inputHash, 10)}
                          </td>
                          <td className="px-5 py-3 text-right">
                            <StatusPill
                              tone={
                                run.status === 'succeeded' ? 'success' : 'critical'
                              }
                            >
                              {labelize(run.status)}
                            </StatusPill>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </QueryBoundary>
          </CardBody>
        </Card>
      </div>
    </>
  );
}
