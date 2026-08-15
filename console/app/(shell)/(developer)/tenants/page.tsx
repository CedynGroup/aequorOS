'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { Download, UserPlus } from 'lucide-react';
import { listTenants, type OperatorTenant } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, DASH } from '@/lib/format';
import {
  Button,
  Chip,
  DataTable,
  EmptyState,
  FreshnessChip,
  IngestionChip,
  KpiStat,
  MonoId,
  PageHeader,
  QueryBoundary,
  SectionCard,
  SsoChip,
  type Column,
} from '@/components/ui';
import { csvFilename, downloadCsv } from '@/components/tenants/util';

/**
 * /tenants — the fleet index (the app's primary institution list).
 * Source: GET /operator/v1/tenants. Every column comes from that payload;
 * missing optional fields render as an em dash, never a fabricated value.
 *
 * Upgrades over the old plain table: sortable columns, a name/BK-/OR- search
 * box, live/empty/stale status filters, pagination, a KPI header, and a
 * client-side CSV export of the currently-filtered set.
 */

type StatusFilter = 'all' | 'live' | 'empty' | 'stale';

/** A tenant is "live" once it has at least one reporting period. */
function isLive(t: OperatorTenant): boolean {
  return t.period_count > 0;
}
function isStale(t: OperatorTenant): boolean {
  return Boolean(t.freshness?.is_stale);
}

function freshnessRank(t: OperatorTenant): number {
  const f = t.freshness;
  if (!f) return 3;
  if (f.is_stale) return 0;
  if (f.modules_reported === 0) return 2;
  return 1;
}
function ssoRank(t: OperatorTenant): number {
  if (t.sso_enabled) return 2;
  if (t.sso_configured) return 1;
  return 0;
}

const FILTERS: { key: StatusFilter; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'live', label: 'Live' },
  { key: 'empty', label: 'Empty' },
  { key: 'stale', label: 'Stale' },
];

export default function TenantsPage() {
  const router = useRouter();
  const { data, error, loading, reload } = useApi(listTenants);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const tenants = data?.tenants ?? [];

  const kpis = useMemo(() => {
    const total = tenants.length;
    const live = tenants.filter(isLive).length;
    const stale = tenants.filter(isStale).length;
    return { total, live, empty: total - live, stale };
  }, [tenants]);

  const filtered = useMemo(() => {
    switch (statusFilter) {
      case 'live':
        return tenants.filter(isLive);
      case 'empty':
        return tenants.filter((t) => !isLive(t));
      case 'stale':
        return tenants.filter(isStale);
      default:
        return tenants;
    }
  }, [tenants, statusFilter]);

  function exportCsv() {
    downloadCsv(
      csvFilename('tenants'),
      [
        'bank_name',
        'bank_id',
        'organization_name',
        'organization_id',
        'jurisdiction',
        'currency',
        'license_type',
        'period_count',
        'latest_period_end',
        'freshness',
        'last_ingestion_status',
        'storage_provider',
        'sso',
      ],
      filtered.map((t) => [
        t.bank_name ?? '',
        t.bank_id ?? '',
        t.organization_name,
        t.organization_id,
        t.jurisdiction_code ?? '',
        t.currency ?? '',
        t.license_type ?? '',
        t.period_count,
        t.latest_period_end ?? '',
        t.freshness?.is_stale ? 'stale' : t.freshness?.modules_reported ? 'fresh' : 'none',
        t.last_ingestion?.status ?? '',
        t.storage_provider ?? '',
        t.sso_enabled ? 'enabled' : t.sso_configured ? 'configured' : 'none',
      ]),
    );
  }

  const columns: Column<OperatorTenant>[] = [
    {
      key: 'bank',
      header: 'Bank',
      sortable: true,
      sortAccessor: (t) => t.bank_name ?? '',
      render: (t) => (
        <div className="min-w-0">
          <Link
            href={`/tenants/${t.organization_id}`}
            className="font-medium text-navy hover:text-action"
            onClick={(e) => e.stopPropagation()}
          >
            {t.bank_name ?? 'No bank yet'}
          </Link>
          <div className="font-mono text-caption text-slate">{t.bank_id ?? DASH}</div>
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
          <div className="truncate text-ink">{t.organization_name}</div>
          <MonoId id={t.organization_id} />
        </div>
      ),
    },
    {
      key: 'jurisdiction',
      header: 'Jur.',
      sortable: true,
      sortAccessor: (t) => t.jurisdiction_code ?? '',
      render: (t) => <span className="text-ink">{t.jurisdiction_code ?? DASH}</span>,
    },
    {
      key: 'currency',
      header: 'Ccy',
      sortable: true,
      sortAccessor: (t) => t.currency ?? '',
      render: (t) => <span className="font-mono text-caption text-ink">{t.currency ?? DASH}</span>,
    },
    {
      key: 'license',
      header: 'License',
      sortable: true,
      sortAccessor: (t) => t.license_type ?? '',
      render: (t) => <span className="text-ink">{t.license_type ?? DASH}</span>,
    },
    {
      key: 'periods',
      header: 'Periods',
      numeric: true,
      sortable: true,
      sortAccessor: (t) => t.period_count,
      render: (t) => (
        <div>
          <span className="text-ink">{t.period_count}</span>
          <div className="text-caption text-slate">latest {fmtDate(t.latest_period_end)}</div>
        </div>
      ),
    },
    {
      key: 'freshness',
      header: 'Freshness',
      sortable: true,
      sortAccessor: freshnessRank,
      render: (t) => <FreshnessChip summary={t.freshness} />,
    },
    {
      key: 'ingestion',
      header: 'Last batch',
      sortable: true,
      sortAccessor: (t) => t.last_ingestion?.status ?? '',
      render: (t) => <IngestionChip summary={t.last_ingestion} />,
    },
    {
      key: 'storage',
      header: 'Storage',
      sortable: true,
      sortAccessor: (t) => t.storage_provider ?? '',
      render: (t) =>
        t.storage_provider ? (
          <Chip mono>{t.storage_provider}</Chip>
        ) : (
          <span className="text-slate-light">{DASH}</span>
        ),
    },
    {
      key: 'sso',
      header: 'SSO',
      sortable: true,
      sortAccessor: ssoRank,
      render: (t) => <SsoChip configured={t.sso_configured} enabled={t.sso_enabled} />,
    },
  ];

  return (
    <div>
      <PageHeader
        title="Tenants"
        subtitle="Every onboarded institution on this deployment, straight from the operator API."
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="secondary"
              size="sm"
              icon={<Download size={14} aria-hidden />}
              onClick={exportCsv}
              disabled={filtered.length === 0}
            >
              Export CSV
            </Button>
            <Link
              href="/onboard"
              className="btn-primary inline-flex items-center justify-center gap-1 rounded-md px-2.5 py-1 text-caption font-medium text-white"
            >
              <UserPlus size={14} aria-hidden /> Onboard
            </Link>
          </div>
        }
      />

      <QueryBoundary loading={loading} error={error} onRetry={reload} context="Loading tenants">
        {tenants.length === 0 ? (
          <SectionCard title="Fleet" noPadding>
            <EmptyState
              title="No tenants on this deployment yet"
              description={
                <>
                  Provision the first institution from{' '}
                  <Link href="/onboard" className="text-action hover:underline">
                    Onboard
                  </Link>
                  . A bank goes live on its first ingestion.
                </>
              }
              action={
                <Link
                  href="/onboard"
                  className="btn-primary inline-flex items-center justify-center gap-1 rounded-md px-2.5 py-1 text-caption font-medium text-white"
                >
                  <UserPlus size={14} aria-hidden /> Onboard a tenant
                </Link>
              }
            />
          </SectionCard>
        ) : (
          <>
            <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
              <KpiStat label="Total tenants" value={kpis.total} />
              <KpiStat
                label="Live"
                value={kpis.live}
                status="ok"
                hint="≥ 1 reporting period"
              />
              <KpiStat
                label="Empty"
                value={kpis.empty}
                status={kpis.empty > 0 ? 'warn' : undefined}
                hint="awaiting first ingestion"
              />
              <KpiStat
                label="Stale"
                value={kpis.stale}
                status={kpis.stale > 0 ? 'crit' : 'ok'}
                hint="live metrics behind"
              />
            </div>

            <SectionCard
              title="Fleet"
              subtitle={`${filtered.length} of ${tenants.length} shown`}
              noPadding
              actions={
                <div className="inline-flex items-center rounded-md border border-border-light bg-surface p-0.5">
                  {FILTERS.map((f) => (
                    <button
                      key={f.key}
                      type="button"
                      onClick={() => setStatusFilter(f.key)}
                      aria-pressed={statusFilter === f.key}
                      className={`rounded px-2.5 py-1 text-micro font-medium ${
                        statusFilter === f.key
                          ? 'bg-surface-raised text-navy shadow-subtle'
                          : 'text-slate hover:text-navy'
                      }`}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              }
            >
              <DataTable
                columns={columns}
                rows={filtered}
                density="compact"
                initialSort={{ key: 'bank', dir: 'asc' }}
                getFilterText={(t) =>
                  [
                    t.bank_name ?? '',
                    t.bank_id ?? '',
                    t.organization_name,
                    t.organization_id,
                    t.jurisdiction_code ?? '',
                    t.currency ?? '',
                    t.license_type ?? '',
                  ].join(' ')
                }
                filterPlaceholder="Search name, BK-…, OR-…"
                pageSize={25}
                onRowClick={(t) => router.push(`/tenants/${t.organization_id}`)}
                emptyMessage="No tenants match this filter."
              />
            </SectionCard>
          </>
        )}
      </QueryBoundary>
    </div>
  );
}
