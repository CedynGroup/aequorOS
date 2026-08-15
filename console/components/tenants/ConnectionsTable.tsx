'use client';

import { useRouter } from 'next/navigation';
import type { ReactNode } from 'react';
import type { DataEngineConnection } from '@/lib/api';
import { fmtTs, relTime, DASH } from '@/lib/format';
import { Chip, DataTable, MonoId, StatusChip, type Column } from '@/components/ui';
import { credentialHealth } from './util';

/**
 * The one data-engine connections table, shared by the cross-tenant Operations
 * board and the per-tenant cockpit. Every column is drawn straight from the
 * DataEngineConnection payload; a credential with no expiry renders a dash, not
 * a fabricated "healthy" state.
 */
export function ConnectionsTable({
  connections,
  orgNameFor,
  showOrg = false,
  pageSize = 25,
  filterPlaceholder = 'Filter connections…',
  emptyMessage = 'No connections.',
}: {
  connections: DataEngineConnection[];
  /** Resolve an org id to a display name (Operations board); omit for a single tenant. */
  orgNameFor?: (orgId: string) => string | undefined;
  /** Show the organization column (cross-tenant view). */
  showOrg?: boolean;
  pageSize?: number;
  filterPlaceholder?: string;
  emptyMessage?: ReactNode;
}) {
  const router = useRouter();

  const columns: Column<DataEngineConnection>[] = [];

  if (showOrg) {
    columns.push({
      key: 'org',
      header: 'Organization',
      sortable: true,
      sortAccessor: (c) => orgNameFor?.(c.organization_id) ?? c.organization_id,
      render: (c) => (
        <div className="min-w-0">
          <div className="truncate text-body text-navy">
            {orgNameFor?.(c.organization_id) ?? 'Unknown organization'}
          </div>
          <MonoId id={c.organization_id} />
        </div>
      ),
    });
  }

  columns.push(
    {
      key: 'connection',
      header: 'Connection',
      sortable: true,
      sortAccessor: (c) => c.display_name,
      render: (c) => (
        <div className="min-w-0">
          <div className="truncate text-body font-medium text-navy">{c.display_name}</div>
          <div className="font-mono text-caption text-slate">
            {c.engine} · {c.system}
          </div>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (c) => c.status,
      render: (c) => <StatusChip value={c.status} />,
    },
    {
      key: 'activity',
      header: 'Last activity',
      sortable: true,
      sortAccessor: (c) => c.last_activity_at ?? '',
      render: (c) =>
        c.last_activity_at ? (
          <span title={fmtTs(c.last_activity_at)}>
            <span className="text-body text-navy/90">{relTime(c.last_activity_at)}</span>
            {c.last_activity_status && (
              <span className="ml-1 text-caption text-slate">({c.last_activity_status})</span>
            )}
          </span>
        ) : (
          <span className="text-slate-light">{DASH}</span>
        ),
    },
    {
      key: 'credential',
      header: 'Credential',
      sortable: true,
      // Sort soonest-expiring first; connections with no expiry sort last.
      sortAccessor: (c) => {
        const t = c.credential_expires_at ? Date.parse(c.credential_expires_at) : NaN;
        return Number.isNaN(t) ? Number.MAX_SAFE_INTEGER : t;
      },
      render: (c) => {
        const health = credentialHealth(c.credential_expires_at);
        if (!health) return <span className="text-slate-light">{DASH}</span>;
        return (
          <Chip tone={health.tone} title={c.credential_expires_at ?? undefined}>
            {health.label}
          </Chip>
        );
      },
    },
  );

  return (
    <DataTable
      columns={columns}
      rows={connections}
      density="compact"
      initialSort={showOrg ? { key: 'org', dir: 'asc' } : { key: 'credential', dir: 'asc' }}
      getFilterText={(c) =>
        [
          c.display_name,
          c.engine,
          c.system,
          c.status,
          c.last_activity_status ?? '',
          c.organization_id,
          orgNameFor?.(c.organization_id) ?? '',
        ].join(' ')
      }
      filterPlaceholder={filterPlaceholder}
      pageSize={pageSize}
      emptyMessage={emptyMessage}
      onRowClick={showOrg ? (c) => router.push(`/tenants/${c.organization_id}`) : undefined}
    />
  );
}
