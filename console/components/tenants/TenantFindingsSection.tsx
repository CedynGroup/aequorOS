'use client';

import type { ApiError, TenantFinding, TenantFindingsResponse } from '@/lib/api';
import { fmtTs, relTime, DASH } from '@/lib/format';
import {
  Chip,
  DataTable,
  EmptyState,
  SectionCard,
  StatusChip,
  toneFor,
  type Column,
} from '@/components/ui';
import { DeepSectionBody } from './DeepSectionBody';

/**
 * The tenant's live findings/alerts: active first, then recently cleared, each
 * with a severity chip, the emitting module + rule, and the human message. The
 * `open_count` from the payload drives the header badge.
 */

// Rank so the most severe, still-open findings sort to the top by default.
const SEVERITY_RANK: Record<string, number> = {
  critical: 0,
  high: 1,
  breach: 1,
  warning: 2,
  medium: 2,
  info: 3,
  low: 3,
};

function severityRank(f: TenantFinding): number {
  return SEVERITY_RANK[f.severity.toLowerCase()] ?? 4;
}

const columns: Column<TenantFinding>[] = [
  {
    key: 'severity',
    header: 'Severity',
    sortable: true,
    sortAccessor: severityRank,
    render: (f) => <Chip tone={toneFor(f.severity)}>{f.severity.replace(/_/g, ' ')}</Chip>,
  },
  {
    key: 'module',
    header: 'Module',
    sortable: true,
    sortAccessor: (f) => f.module,
    render: (f) => (
      <div className="min-w-0">
        <div className="font-mono text-caption text-navy">{f.module}</div>
        <div className="font-mono text-micro text-slate" title={f.rule_id}>
          {f.rule_id}
        </div>
      </div>
    ),
  },
  {
    key: 'message',
    header: 'Message',
    render: (f) => (
      <div className="min-w-0">
        <span className="break-words text-body text-navy/90">{f.message || DASH}</span>
        {f.metric && (
          <div className="font-mono text-micro text-slate">metric: {f.metric}</div>
        )}
      </div>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    sortable: true,
    sortAccessor: (f) => f.status,
    render: (f) => <StatusChip value={f.status} />,
  },
  {
    key: 'updated',
    header: 'Updated',
    align: 'right',
    sortable: true,
    sortAccessor: (f) => f.updated_at,
    render: (f) => (
      <span className="whitespace-nowrap text-caption text-slate" title={fmtTs(f.updated_at)}>
        {relTime(f.updated_at)}
      </span>
    ),
  },
];

export function TenantFindingsSection({
  data,
  loading,
  error,
  reload,
  orgId,
  orgLabel,
}: {
  data: TenantFindingsResponse | null;
  loading: boolean;
  error: ApiError | null;
  reload: () => void;
  orgId: string;
  orgLabel?: string;
}) {
  const findings = data?.findings ?? [];
  const openCount = data?.open_count ?? 0;

  return (
    <SectionCard
      title="Findings"
      subtitle={`${openCount} open · ${findings.length} shown`}
      noPadding
      actions={
        openCount > 0 ? (
          <Chip tone={openCount > 0 ? 'warn' : 'neutral'}>{openCount} open</Chip>
        ) : undefined
      }
    >
      <DeepSectionBody
        loading={loading && !data}
        error={error}
        reload={reload}
        context="Loading findings"
        orgId={orgId}
        orgLabel={orgLabel}
        showEmpty={findings.length === 0}
        empty={
          <EmptyState
            title="No findings"
            description="Rule-engine alerts (breaches, early-warning indicators, data-quality flags) appear here as the tenant's data is evaluated."
          />
        }
      >
        <DataTable
          columns={columns}
          rows={findings}
          density="compact"
          initialSort={{ key: 'severity', dir: 'asc' }}
          getFilterText={(f) =>
            [f.severity, f.module, f.rule_id, f.message, f.metric ?? '', f.status].join(' ')
          }
          filterPlaceholder="Filter findings…"
          pageSize={12}
          emptyMessage="No findings match this filter."
        />
      </DeepSectionBody>
    </SectionCard>
  );
}
