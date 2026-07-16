'use client';

/**
 * Regulatory Reporting — Calendar (hub landing). The deadline board: every
 * registry obligation in the horizon with its RAG grade, linked package
 * state, and an Act 930 penalty-exposure note for overdue rows. Rows deep-
 * link into the Returns workspace for that return + reporting date.
 */

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { CalendarClock, TriangleAlert } from 'lucide-react';
import type { ReportingObligationRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import DataTable, { type Column } from '@/components/ui/DataTable';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { useBankContext } from '@/components/shell/BankContext';
import { useReportingObligations } from '@/lib/api/hooks';
import { fmtDateUTC, isoDate } from '@/lib/api/values';
import {
  FAMILY_LABELS,
  PACKAGE_STATUS_LABELS,
  PENALTY_FOOTNOTE,
  RagPill,
  indicativePenaltyGhs,
  returnsHref,
} from '@/components/submissions/shared';

const HORIZON_OPTIONS = [3, 6, 12];

/** Whether an obligation is a downtime email submission awaiting ORASS
 * re-upload: submitted but still not satisfying its RAG (BG/FMD/2026/07). */
function isPendingReupload(obligation: ReportingObligationRead): boolean {
  return obligation.packageStatus === 'submitted' && obligation.rag !== 'on_track';
}

function daysOverdue(dueDate: Date, asOf: Date): number {
  return Math.max(
    Math.floor((asOf.getTime() - dueDate.getTime()) / 86_400_000),
    0
  );
}

export default function RegulatoryCalendarPage() {
  const router = useRouter();
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const [horizon, setHorizon] = useState(3);

  const query = useReportingObligations(bankId, horizon);
  const obligations = query.data?.obligations ?? [];
  const asOf = query.data?.asOf;

  const overdue = obligations.filter((o) => o.rag === 'overdue');
  const dueSoon = obligations.filter((o) => o.rag === 'due_soon');
  const onTrack = obligations.filter((o) => o.rag === 'on_track');
  const pendingReupload = obligations.filter(isPendingReupload);

  const columns: Column<ReportingObligationRead>[] = [
    {
      key: 'return',
      header: 'Return',
      render: (o) => (
        <div className="min-w-0">
          <p className="font-mono text-caption font-medium text-navy">
            {o.returnCode}
          </p>
          <p className="text-caption text-slate truncate max-w-[320px]">
            {o.title}
          </p>
        </div>
      ),
    },
    {
      key: 'family',
      header: 'Family',
      render: (o) => (
        <span className="inline-flex items-center px-2 py-0.5 rounded border border-border bg-surface text-caption text-slate">
          {FAMILY_LABELS[o.returnFamily] ?? o.returnFamily}
        </span>
      ),
    },
    {
      key: 'frequency',
      header: 'Frequency',
      render: (o) => <span className="text-navy/85 capitalize">{o.frequency}</span>,
    },
    {
      key: 'reportingDate',
      header: 'Reporting date',
      render: (o) => (
        <span className="font-mono text-caption text-navy/85 tnum">
          {fmtDateUTC(o.reportingDate)}
        </span>
      ),
    },
    {
      key: 'dueDate',
      header: 'Due date',
      render: (o) => (
        <span
          className={`font-mono text-caption tnum ${
            o.rag === 'overdue' ? 'text-critical font-medium' : 'text-navy/85'
          }`}
        >
          {fmtDateUTC(o.dueDate)}
        </span>
      ),
    },
    {
      key: 'rag',
      header: 'Status',
      render: (o) => (
        <span className="inline-flex items-center gap-2">
          <RagPill rag={o.rag} />
          {isPendingReupload(o) && (
            <span className="text-micro text-warning font-medium uppercase tracking-wider whitespace-nowrap">
              ORASS re-upload pending
            </span>
          )}
        </span>
      ),
    },
    {
      key: 'package',
      header: 'Package',
      render: (o) =>
        o.packageStatus ? (
          <span className="text-caption text-navy/85 whitespace-nowrap">
            {PACKAGE_STATUS_LABELS[o.packageStatus] ?? o.packageStatus}
            <span className="ml-1.5 font-mono text-micro text-slate tnum">
              v{o.packageVersion}
            </span>
          </span>
        ) : (
          <span className="text-caption text-slate">Not generated</span>
        ),
    },
  ];

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Governance', href: '/submissions' },
          { label: 'Regulatory Reporting' },
          { label: 'Calendar' },
        ]}
        title="Regulatory Reporting"
        subtitle="Bank of Ghana deadline board · every official return, its due date, and its package state"
        asOf={asOf ? fmtDateUTC(asOf) : undefined}
        action={
          <label className="flex items-center gap-2 text-caption text-slate">
            Horizon
            <select
              value={horizon}
              onChange={(e) => setHorizon(Number(e.target.value))}
              className="rounded border border-border bg-surface-raised px-2 py-1.5 text-caption text-navy"
            >
              {HORIZON_OPTIONS.map((months) => (
                <option key={months} value={months}>
                  {months} months
                </option>
              ))}
            </select>
          </label>
        }
      />

      <div className="px-8 py-6 space-y-6">
        <QueryBoundary
          isLoading={query.isLoading}
          error={query.error}
          onRetry={() => query.refetch()}
        >
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <KpiStat
              label="Overdue"
              value={overdue.length}
              status={overdue.length > 0 ? 'crit' : 'ok'}
              hint="Deadline passed without a completed submission"
            />
            <KpiStat
              label="Due soon"
              value={dueSoon.length}
              status={dueSoon.length > 0 ? 'warn' : 'ok'}
              hint="Due within 7 days"
            />
            <KpiStat
              label="On track"
              value={onTrack.length}
              status="ok"
              hint="Submitted, acknowledged, or not yet near deadline"
            />
            <KpiStat
              label="Pending ORASS re-upload"
              value={pendingReupload.length}
              status={pendingReupload.length > 0 ? 'warn' : 'ok'}
              hint="Downtime email submissions awaiting ORASS (BG/FMD/2026/07)"
            />
          </div>

          <SectionCard
            title="Reporting obligations"
            subtitle={`Registry obligations for the next ${horizon} months — click a row to open its Returns workspace`}
            noPadding
            footer={
              <span className="inline-flex items-start gap-1.5 max-w-3xl leading-relaxed">
                <CalendarClock size={11} className="mt-0.5 shrink-0" aria-hidden />
                {PENALTY_FOOTNOTE}
              </span>
            }
          >
            {obligations.length === 0 ? (
              <div className="p-5">
                <EmptyState
                  title="No obligations in this horizon"
                  description="Widen the horizon to see upcoming reporting dates from the return registry."
                />
              </div>
            ) : (
              <DataTable
                columns={columns}
                rows={obligations}
                density="compact"
                rowClassName={(o) =>
                  o.rag === 'overdue' ? 'bg-critical-light/20' : ''
                }
                onRowClick={(o) =>
                  router.push(returnsHref(o.returnCode, isoDate(o.reportingDate)))
                }
              />
            )}
          </SectionCard>

          {overdue.length > 0 && asOf && (
            <SectionCard
              title="Indicative penalty exposure — overdue returns"
              subtitle="Act 930 s.93(3) · units × GH¢12/unit (Fines (Penalty Units) Act 572) · indicative only"
            >
              <ul className="space-y-2">
                {overdue.map((o) => {
                  const days = daysOverdue(o.dueDate, asOf);
                  const penalty = indicativePenaltyGhs(days);
                  return (
                    <li
                      key={`${o.returnCode}-${isoDate(o.reportingDate)}`}
                      className="flex items-start gap-2.5 rounded border border-critical/20 bg-critical-light/30 px-3.5 py-2.5"
                    >
                      <TriangleAlert
                        size={14}
                        className="text-critical shrink-0 mt-0.5"
                        aria-hidden
                      />
                      <div className="min-w-0 text-body">
                        <p className="font-medium text-navy">
                          <span className="font-mono">{o.returnCode}</span> ·{' '}
                          {fmtDateUTC(o.reportingDate)} — due{' '}
                          {fmtDateUTC(o.dueDate)}{' '}
                          <span className="font-mono tnum">
                            ({days} {days === 1 ? 'day' : 'days'} overdue)
                          </span>
                        </p>
                        <p className="mt-0.5 text-caption text-navy/80 tnum">
                          Up to GH¢{penalty.baseGhs.toLocaleString('en-GB')} (500
                          units) on the institution and responsible officers,
                          plus GH¢{penalty.dailyGhs.toLocaleString('en-GB')}/day
                          (50 units) ×{' '}
                          <span className="font-mono">{days}</span> days ≈ GH¢
                          {penalty.runningGhs.toLocaleString('en-GB')} running —
                          indicative.
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ul>
            </SectionCard>
          )}
        </QueryBoundary>
      </div>
    </>
  );
}
