'use client';

import { useRouter } from 'next/navigation';
import {
  Chip,
  type Column,
  DataTable,
  EmptyState,
  ErrorPanel,
  FreshnessChip,
  IngestionChip,
  MonoId,
  SectionCard,
  SkeletonTable,
  SsoChip,
} from '@/components/ui';
import { ApiError, type OperatorTenant } from '@/lib/api';
import { DASH, fmtDate } from '@/lib/format';

/**
 * Fleet snapshot: the whole tenant inventory from `listTenants()` as one
 * sortable, filterable, paginated table. Rows deep-link to the tenant detail
 * page. Loading/error/empty are handled inside the SectionCard so the header
 * stays visible while data streams in.
 */
export function FleetSnapshot({
  tenants,
  loading,
  error,
  onRetry,
}: {
  tenants: OperatorTenant[] | null;
  loading: boolean;
  error: ApiError | null;
  onRetry: () => void;
}) {
  const router = useRouter();

  const columns: Column<OperatorTenant>[] = [
    {
      key: 'bank',
      header: 'Bank',
      sortable: true,
      sortAccessor: (t) => t.bank_name ?? '',
      render: (t) => (
        <div className="min-w-0">
          <p className="truncate font-medium text-navy">{t.bank_name ?? 'No bank yet'}</p>
          <p className="font-mono text-caption text-slate">{t.bank_id ?? DASH}</p>
        </div>
      ),
    },
    {
      key: 'org',
      header: 'Organization',
      sortable: true,
      sortAccessor: (t) => t.organization_name,
      render: (t) => (
        <div className="min-w-0">
          <p className="truncate text-ink">{t.organization_name}</p>
          {/* Stop the copy click from also triggering row navigation. */}
          <span onClick={(e) => e.stopPropagation()}>
            <MonoId id={t.organization_id} />
          </span>
        </div>
      ),
    },
    {
      key: 'jurisdiction',
      header: 'Juris.',
      sortable: true,
      sortAccessor: (t) => t.jurisdiction_code ?? '',
      render: (t) => <span className="text-ink">{t.jurisdiction_code ?? DASH}</span>,
    },
    {
      key: 'currency',
      header: 'Ccy',
      sortable: true,
      sortAccessor: (t) => t.currency ?? '',
      render: (t) => (
        <span className="font-mono text-caption text-ink">{t.currency ?? DASH}</span>
      ),
    },
    {
      key: 'periods',
      header: 'Periods',
      numeric: true,
      sortable: true,
      sortAccessor: (t) => t.period_count,
      render: (t) => (
        <div className="text-right">
          <span className="text-ink">{t.period_count}</span>
          <div className="text-caption text-slate">latest {fmtDate(t.latest_period_end)}</div>
        </div>
      ),
    },
    {
      key: 'freshness',
      header: 'Freshness',
      sortable: true,
      // fresh (0) < stale (1) < no summary (2)
      sortAccessor: (t) => (t.freshness ? (t.freshness.is_stale ? 1 : 0) : 2),
      render: (t) => <FreshnessChip summary={t.freshness} />,
    },
    {
      key: 'ingestion',
      header: 'Last batch',
      render: (t) => <IngestionChip summary={t.last_ingestion} />,
    },
    {
      key: 'sso',
      header: 'SSO',
      render: (t) => <SsoChip configured={t.sso_configured} enabled={t.sso_enabled} />,
    },
  ];

  return (
    <SectionCard
      title="Fleet snapshot"
      subtitle="Every onboarded institution — freshness, last ingestion, and SSO at a glance."
      actions={tenants ? <Chip tone="neutral">{tenants.length}</Chip> : undefined}
      noPadding
    >
      {loading ? (
        <SkeletonTable rows={6} />
      ) : error ? (
        <div className="p-4">
          <ErrorPanel error={error} onRetry={onRetry} context="Loading fleet" />
        </div>
      ) : !tenants || tenants.length === 0 ? (
        <EmptyState
          title="No tenants on this deployment yet"
          description="A bank goes live on its first ingestion."
        />
      ) : (
        <DataTable
          columns={columns}
          rows={tenants}
          density="compact"
          stickyHeader
          maxHeight="60vh"
          onRowClick={(t) => router.push(`/tenants/${t.organization_id}`)}
          getFilterText={(t) =>
            [
              t.bank_name,
              t.bank_id,
              t.organization_name,
              t.organization_id,
              t.jurisdiction_code,
              t.currency,
              t.license_type,
            ]
              .filter(Boolean)
              .join(' ')
          }
          filterPlaceholder="Search bank, org, jurisdiction, currency…"
          initialSort={{ key: 'bank', dir: 'asc' }}
          pageSize={12}
        />
      )}
    </SectionCard>
  );
}
