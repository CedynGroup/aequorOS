'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { ArrowLeft } from 'lucide-react';
import { getTenantActivity, listDataEngines, listTenants } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import {
  Chip,
  EmptyState,
  ErrorPanel,
  FieldRow,
  FreshnessChip,
  IngestionChip,
  MonoId,
  Skeleton,
  SkeletonRows,
  SsoChip,
  StatusChip,
} from '@/components/ui';

/**
 * /tenants/[orgId] — one institution.
 * Sources:
 *  - GET /operator/v1/tenants (there is no single-tenant endpoint yet; the
 *    header card is the matching row from the list)
 *  - GET /operator/v1/tenants/{orgId}/activity?limit=100
 *  - GET /operator/v1/data-engines filtered to this organization_id
 */
export default function TenantDetailPage() {
  const params = useParams<{ orgId: string }>();
  const orgId = params.orgId;

  const tenants = useApi(listTenants, [orgId]);
  const activity = useApi(() => getTenantActivity(orgId, 100), [orgId]);
  const engines = useApi(listDataEngines, [orgId]);

  const tenant = tenants.data?.tenants.find((t) => t.organization_id === orgId) ?? null;
  const connections =
    engines.data?.connections.filter((c) => c.organization_id === orgId) ?? null;

  return (
    <div>
      <Link
        href="/tenants"
        className="mb-4 inline-flex items-center gap-1 text-caption text-slate hover:text-ink"
      >
        <ArrowLeft size={13} /> All tenants
      </Link>

      {/* ------------------------------------------------ header card */}
      {tenants.loading && (
        <div className="card mb-6 space-y-3 p-5">
          <Skeleton className="h-7 w-64" />
          <Skeleton className="h-4 w-96" />
          <Skeleton className="h-4 w-80" />
        </div>
      )}
      {tenants.error && (
        <div className="mb-6">
          <ErrorPanel error={tenants.error} onRetry={tenants.reload} context="Loading tenant" />
        </div>
      )}
      {tenants.data && !tenant && (
        <div className="card mb-6">
          <EmptyState
            title={`No tenant with organization id ${orgId}`}
            hint="The operator API does not know this organization. Check the id, or refresh the tenant list."
          />
        </div>
      )}
      {tenant && (
        <div className="card mb-6 p-5">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <h1 className="text-h1 text-navy">{tenant.bank_name ?? 'No bank yet'}</h1>
              <p className="mb-2 mt-0.5 text-body text-slate">{tenant.organization_name}</p>
              <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
                {tenant.bank_id && (
                  <span className="text-caption text-slate">
                    Bank&nbsp;
                    <MonoId id={tenant.bank_id} />
                  </span>
                )}
                <span className="text-caption text-slate">
                  Org&nbsp;
                  <MonoId id={tenant.organization_id} />
                </span>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <FreshnessChip summary={tenant.freshness} />
              <SsoChip configured={tenant.sso_configured} enabled={tenant.sso_enabled} />
              {tenant.storage_provider && <Chip mono>{tenant.storage_provider}</Chip>}
            </div>
          </div>

          <div className="mt-4 grid gap-x-10 border-t border-border-light pt-3 sm:grid-cols-2 lg:grid-cols-3">
            <FieldRow label="Jurisdiction">{tenant.jurisdiction_code ?? DASH}</FieldRow>
            <FieldRow label="Currency">
              <span className="font-mono">{tenant.currency ?? DASH}</span>
            </FieldRow>
            <FieldRow label="License">{tenant.license_type ?? DASH}</FieldRow>
            <FieldRow label="Reporting periods">
              <span className="font-mono">{tenant.period_count}</span>
            </FieldRow>
            <FieldRow label="Latest period end">{fmtDate(tenant.latest_period_end)}</FieldRow>
            <FieldRow label="Org created">
              <span title={fmtTs(tenant.organization_created_at)}>
                {fmtDate(tenant.organization_created_at)}
              </span>
            </FieldRow>
            <FieldRow label="Bank created">
              <span title={fmtTs(tenant.bank_created_at)}>{fmtDate(tenant.bank_created_at)}</span>
            </FieldRow>
            <FieldRow label="Last ingestion">
              <IngestionChip summary={tenant.last_ingestion} />
            </FieldRow>
            {tenant.last_ingestion && (
              <FieldRow label="Ingestion source">
                <span className="font-mono">{tenant.last_ingestion.source_system}</span>
                {' · as of '}
                {fmtDate(tenant.last_ingestion.as_of_date)}
              </FieldRow>
            )}
          </div>
        </div>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        {/* ------------------------------------------------ activity feed */}
        <section className="card">
          <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">Activity</h2>
          {activity.loading && <SkeletonRows rows={6} />}
          {activity.error && (
            <div className="p-4">
              <ErrorPanel
                error={activity.error}
                onRetry={activity.reload}
                context="Loading activity"
              />
            </div>
          )}
          {activity.data && activity.data.items.length === 0 && (
            <EmptyState
              title="No activity recorded"
              hint="Ingestions, jobs, official runs, packages, and audit events will appear here as the tenant is used."
            />
          )}
          {activity.data && activity.data.items.length > 0 && (
            <ul className="divide-y divide-border-light">
              {activity.data.items.map((item, i) => (
                <li key={`${item.ts}-${i}`} className="flex items-start gap-3 px-5 py-3">
                  <span
                    className="w-16 shrink-0 pt-0.5 text-caption text-slate"
                    title={fmtTs(item.ts)}
                  >
                    {relTime(item.ts)}
                  </span>
                  <Chip mono>{item.kind || DASH}</Chip>
                  <span className="min-w-0 flex-1 break-words text-body text-ink">
                    {item.summary || DASH}
                  </span>
                  <StatusChip value={item.status} />
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* ------------------------------------------------ data-engine connections */}
        <section className="card">
          <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">
            Data-engine connections
          </h2>
          {engines.loading && <SkeletonRows rows={3} />}
          {engines.error && (
            <div className="p-4">
              <ErrorPanel
                error={engines.error}
                onRetry={engines.reload}
                context="Loading connections"
              />
            </div>
          )}
          {connections && connections.length === 0 && (
            <EmptyState
              title="No connections for this org"
              hint="Market-data, Database-Direct, and core-banking (T24) connections show up here once the bank's Data Engine is configured."
            />
          )}
          {connections && connections.length > 0 && (
            <ul className="divide-y divide-border-light">
              {connections.map((c, i) => (
                <li key={`${c.display_name}-${i}`} className="flex items-center gap-3 px-5 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-body font-medium text-navy">
                      {c.display_name}
                    </div>
                    <div className="text-caption text-slate">
                      <span className="font-mono">
                        {c.engine} · {c.system}
                      </span>
                      {' · last activity '}
                      <span title={fmtTs(c.last_activity_at)}>{relTime(c.last_activity_at)}</span>
                      {c.last_activity_status ? ` (${c.last_activity_status})` : ''}
                    </div>
                    {c.credential_expires_at && (
                      <div className="text-caption text-warning">
                        credential expires {relTime(c.credential_expires_at)}
                      </div>
                    )}
                  </div>
                  <StatusChip value={c.status} />
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
