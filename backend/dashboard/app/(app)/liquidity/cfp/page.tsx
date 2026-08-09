'use client';

import Link from 'next/link';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import QueryBoundary from '@/components/ui/QueryBoundary';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { useBankContext } from '@/components/shell/BankContext';
import { useCfpEvents, useCfpSummary, useEwiDashboard } from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';
import { regShort } from '@/lib/format';
import type { EwiEvaluationRead } from '@aequoros/risk-service-api';

// Server-side EWI states (LRMD ¶28(e)–(f)): values, Board trigger levels and
// RAG classifications all come from the engine — nothing is derived here.
const STATUS_TONE: Record<string, StatusTone> = {
  normal: 'success',
  watch: 'amber',
  action: 'critical',
  unconfigured: 'slate',
  no_data: 'slate',
};

const STATUS_LABEL: Record<string, string> = {
  normal: 'Normal',
  watch: 'Watch',
  action: 'Action',
  unconfigured: 'Unconfigured',
  no_data: 'No data',
};

const ESCALATION_COPY: Record<string, { label: string; status: 'ok' | 'warn' | 'crit' }> = {
  normal: { label: 'Business as usual', status: 'ok' },
  heightened_monitoring: { label: 'Heightened monitoring', status: 'warn' },
  escalation: { label: 'Escalation — action trigger breached', status: 'crit' },
  cfp_active: { label: 'CFP ACTIVE', status: 'crit' },
};

function fmtValue(indicator: EwiEvaluationRead): string {
  if (indicator.value === null || indicator.value === undefined) return '—';
  const value = Number(indicator.value);
  if (indicator.unit === 'count') return String(Math.round(value));
  if (indicator.unit === 'days') return `${value.toFixed(0)} days`;
  return `${value.toFixed(2)}%`;
}

function fmtThreshold(indicator: EwiEvaluationRead, level: unknown): string {
  if (level === null || level === undefined) return 'Not set';
  const op = indicator.direction === 'below' ? '<' : '≥';
  const value = Number(level);
  const unit = indicator.unit === 'count' ? '' : indicator.unit === 'days' ? ' days' : '%';
  return `${op} ${value}${unit}`;
}

const indicatorColumns: Column<EwiEvaluationRead>[] = [
  {
    key: 'name',
    header: 'Early-warning indicator',
    width: '32%',
    render: (r) => (
      <div>
        <p className="font-medium text-navy">{r.name}</p>
        <p className="text-caption text-slate">{r.metricBasis}</p>
        {r.recoveryPlanReference ? (
          <p className="text-caption text-slate/80">
            Recovery plan: {String(r.recoveryPlanReference)}
          </p>
        ) : null}
      </div>
    ),
  },
  { key: 'current', header: 'Current', numeric: true, render: (r) => fmtValue(r) },
  {
    key: 'prior',
    header: 'Prior period',
    numeric: true,
    render: (r) =>
      r.priorValue === null || r.priorValue === undefined
        ? '—'
        : fmtValue({ ...r, value: r.priorValue }),
  },
  {
    key: 'watch',
    header: 'Watch trigger',
    numeric: true,
    render: (r) => fmtThreshold(r, r.watchThreshold),
  },
  {
    key: 'action',
    header: 'Action trigger',
    numeric: true,
    render: (r) => fmtThreshold(r, r.actionThreshold),
  },
  {
    key: 'status',
    header: 'Status',
    align: 'right',
    render: (r) => (
      <div className="flex flex-col items-end gap-1">
        <StatusPill tone={STATUS_TONE[r.status] ?? 'slate'}>
          {STATUS_LABEL[r.status] ?? r.status}
        </StatusPill>
        {r.detail ? (
          <span className="text-caption text-slate text-right max-w-[220px]">{r.detail}</span>
        ) : null}
      </div>
    ),
  },
];

export default function ContingencyFundingPlan() {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const ewis = useEwiDashboard(bankId, periodId);
  const cfp = useCfpSummary(bankId);
  const events = useCfpEvents(bankId);

  const dashboard = ewis.data;
  const approved = cfp.data?.approved ?? null;
  const escalation = dashboard
    ? ESCALATION_COPY[dashboard.escalationState] ?? ESCALATION_COPY.normal
    : ESCALATION_COPY.normal;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Liquidity Risk', href: '/liquidity' },
          { label: 'CFP' },
        ]}
        title="Contingency Funding Plan"
        subtitle={`Server-side EWI framework (LRMD ¶28) · CFP lifecycle with ${regShort()} ¶74 notification`}
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
      />

      <QueryBoundary
        isLoading={ewis.isLoading}
        error={ewis.error}
        onRetry={() => ewis.refetch()}
      >
        {dashboard && (
          <div className="px-8 py-6 space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
              <KpiStat
                label="Escalation state"
                value={escalation.label}
                status={escalation.status}
                hint="Computed server-side from indicator states"
              />
              <KpiStat
                label="Indicators at action"
                value={String(
                  dashboard.indicators.filter((entry) => entry.status === 'action').length
                )}
                status={
                  dashboard.indicators.some((entry) => entry.status === 'action')
                    ? 'crit'
                    : 'ok'
                }
                hint={`${dashboard.indicators.filter((entry) => entry.status === 'watch').length} at watch`}
              />
              <KpiStat
                label="Board-approved CFP"
                value={approved ? `v${approved.version}` : 'None'}
                // No approved plan is an absence-of-data state, not a breach —
                // neutral card, no status edge.
                status={approved ? (approved.approvalOverdue ? 'warn' : 'ok') : undefined}
                hint={
                  approved
                    ? approved.approvalOverdue
                      ? 'Annual re-approval overdue (¶71)'
                      : `Approval valid to ${approved.approvalExpiresAt ? fmtDateUTC(new Date(String(approved.approvalExpiresAt))) : '—'}`
                    : 'Approve a plan before activation is possible'
                }
              />
              <KpiStat
                label="CFP activation"
                value={dashboard.cfpActive ? 'ACTIVE' : 'Not active'}
                status={dashboard.cfpActive ? 'crit' : 'ok'}
                hint={`Activation notifies ${regShort()} (LRMD ¶74)`}
              />
            </div>

            <SectionCard
              title="Early-warning indicators"
              subtitle="The eight directive starter indicators plus any Board additions — values, trigger levels and states computed by the engine"
              noPadding
              footer={
                <span>
                  Trigger levels are Board configuration with approval evidence; an
                  indicator without levels shows Unconfigured rather than an invented
                  classification. {' '}
                  <Link href="/liquidity/monitoring" className="text-action hover:underline">
                    Monitoring tools & threshold register
                  </Link>
                </span>
              }
            >
              <DataTable columns={indicatorColumns} rows={dashboard.indicators} />
            </SectionCard>

            <SectionCard
              title="Activation log"
              subtitle="¶74 events — each carries the EWI snapshot at event time and the regulator-notification evidence"
              noPadding
              footer={
                <span>
                  Activation and de-escalation are approver-gated Board acts recorded
                  append-only; the plan document itself (¶72(a)–(g) contents) is
                  maintained through the CFP API workspace.
                </span>
              }
            >
              {events.data && events.data.events.length > 0 ? (
                <ul className="divide-y divide-border-light">
                  {events.data.events.map((event) => (
                    <li key={event.id} className="px-5 py-4 flex items-start gap-4">
                      <StatusPill
                        tone={event.eventType === 'activated' ? 'critical' : 'success'}
                      >
                        {event.eventType === 'activated' ? 'Activated' : 'De-escalated'}
                      </StatusPill>
                      <div className="min-w-0 flex-1">
                        <p className="text-body text-navy">{event.reason}</p>
                        <p className="mt-1 text-caption text-slate">
                          CFP v{event.cfpVersion} · {fmtDateUTC(event.createdAt)}
                          {event.approvalOverdue
                            ? ' · Board approval was overdue at event time (¶71)'
                            : ''}
                          {event.regulatorNotificationId
                            ? ' · regulator-notification recorded'
                            : ''}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="px-5 py-6 text-body text-slate">
                  No activation events. The CFP has never been triggered for this
                  institution.
                </p>
              )}
            </SectionCard>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
