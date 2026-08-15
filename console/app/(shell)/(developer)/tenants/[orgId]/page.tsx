'use client';

import { useState } from 'react';
import { useParams } from 'next/navigation';
import { ChevronDown, RefreshCw } from 'lucide-react';
import {
  getTenant,
  getTenantActivity,
  getTenantConfig,
  getTenantEntitlements,
  getTenantFindings,
  getTenantIngestion,
  getTenantMetrics,
  getTenantStorage,
  getTenantUsers,
  listDataEngines,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { useInspector } from '@/lib/inspector';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import {
  Button,
  Chip,
  DataTable,
  EmptyState,
  FieldRow,
  FreshnessChip,
  KpiStat,
  MonoId,
  PageHeader,
  QueryBoundary,
  SectionCard,
  SkeletonRows,
  SsoChip,
  StatusChip,
  toneFor,
  type Column,
} from '@/components/ui';
import { ConnectionsTable } from '@/components/tenants/ConnectionsTable';
import { InspectTenantButton } from '@/components/tenants/InspectTenantButton';
import { OpenBankDashboardButton } from '@/components/tenants/OpenBankDashboardButton';
import { DeepSectionBody, GatedTenantData } from '@/components/tenants/DeepSectionBody';
import { TenantMetricsSection } from '@/components/tenants/TenantMetricsSection';
import { TenantFindingsSection } from '@/components/tenants/TenantFindingsSection';
import { TenantIngestionSection } from '@/components/tenants/TenantIngestionSection';
import { TenantConfigSection } from '@/components/tenants/TenantConfigSection';
import { RemediationPanel } from '@/components/tenants/RemediationPanel';
import { formatBytes } from '@/components/tenants/util';
import type { DeskEntitlement, TenantActivityItem, TenantUser } from '@/lib/api';

/**
 * /tenants/[orgId] — the Tenant Inspector cockpit.
 *
 * Two tiers, split on the inspection session:
 *  - HEADER + KPI row render ALWAYS from the OPEN metadata endpoint
 *    (GET /operator/v1/tenants/{orgId} → TenantRead). No session needed.
 *  - DEEP sections (activity, metrics, findings, ingestion, users,
 *    entitlements, connections, config, storage) are backend-gated behind an
 *    ACTIVE inspector session for THIS org (403 `inspection_required`
 *    otherwise). We mirror that gate in the UI: the deep reads only fire while
 *    a session is active (session-presence dep), and when none is active a
 *    single placeholder with the "Inspect this tenant" CTA replaces the region.
 */

/** Map an API status string onto the KpiStat edge-glow vocabulary. */
function kpiStatusFor(value: string | null | undefined): 'ok' | 'warn' | 'crit' | undefined {
  const tone = toneFor(value);
  return tone === 'ok' || tone === 'warn' || tone === 'crit' ? tone : undefined;
}

export default function TenantDetailPage() {
  const params = useParams<{ orgId: string }>();
  const orgId = params.orgId;

  const { active } = useInspector();
  // The context's `active` already drops expired sessions; this org must match.
  const sessionActive = active?.organization_id === orgId;

  const [activityLimit, setActivityLimit] = useState(50);

  // Header + KPIs — OPEN endpoint, always fetched.
  const tenant = useApi(() => getTenant(orgId), [orgId]);

  // Deep reads — session-gated. Short-circuit to a resolved null when no session
  // so we never fire a guaranteed 403; the `sessionActive` dep re-runs each read
  // the moment a session starts, so the sections populate immediately.
  const activity = useApi(
    () => (sessionActive ? getTenantActivity(orgId, activityLimit) : Promise.resolve(null)),
    [orgId, activityLimit, sessionActive],
  );
  const metrics = useApi(
    () => (sessionActive ? getTenantMetrics(orgId) : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const findings = useApi(
    () => (sessionActive ? getTenantFindings(orgId, 100) : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const ingestion = useApi(
    () => (sessionActive ? getTenantIngestion(orgId, 100) : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const users = useApi(
    () => (sessionActive ? getTenantUsers(orgId) : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const entitlements = useApi(
    () => (sessionActive ? getTenantEntitlements(orgId) : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const storage = useApi(
    () => (sessionActive ? getTenantStorage(orgId) : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const engines = useApi(
    () => (sessionActive ? listDataEngines() : Promise.resolve(null)),
    [orgId, sessionActive],
  );
  const config = useApi(
    () => (sessionActive ? getTenantConfig(orgId) : Promise.resolve(null)),
    [orgId, sessionActive],
  );

  const t = tenant.data;
  const orgLabel = t?.bank_name ?? t?.organization_name ?? undefined;
  const connections = (engines.data?.connections ?? []).filter((c) => c.organization_id === orgId);

  function reloadAll() {
    tenant.reload();
    activity.reload();
    metrics.reload();
    findings.reload();
    ingestion.reload();
    users.reload();
    entitlements.reload();
    storage.reload();
    engines.reload();
    config.reload();
  }

  // ---- activity ----------------------------------------------------------
  const activityItems = activity.data?.items ?? [];
  const activityHasMore = activityItems.length >= activityLimit;
  const activityColumns: Column<TenantActivityItem>[] = [
    {
      key: 'ts',
      header: 'When',
      width: '120px',
      sortable: true,
      sortAccessor: (i) => i.ts,
      render: (i) => (
        <span className="whitespace-nowrap text-caption text-slate" title={fmtTs(i.ts)}>
          {relTime(i.ts)}
        </span>
      ),
    },
    {
      key: 'kind',
      header: 'Kind',
      sortable: true,
      sortAccessor: (i) => i.kind,
      render: (i) =>
        i.kind ? <Chip mono>{i.kind}</Chip> : <span className="text-slate-light">{DASH}</span>,
    },
    {
      key: 'summary',
      header: 'Summary',
      render: (i) => <span className="break-words text-body text-navy/90">{i.summary || DASH}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      align: 'right',
      sortable: true,
      sortAccessor: (i) => i.status,
      render: (i) => <StatusChip value={i.status} />,
    },
  ];

  // ---- users -------------------------------------------------------------
  const userRows = users.data?.users ?? [];
  const userColumns: Column<TenantUser>[] = [
    {
      key: 'email',
      header: 'User',
      sortable: true,
      sortAccessor: (u) => u.email,
      render: (u) => (
        <div className="min-w-0">
          <div className="font-mono text-caption text-navy">{u.email}</div>
          {u.full_name && <div className="text-caption text-slate">{u.full_name}</div>}
        </div>
      ),
    },
    {
      key: 'role',
      header: 'Role',
      sortable: true,
      sortAccessor: (u) => u.role,
      render: (u) => <Chip tone="neutral">{u.role.replace(/_/g, ' ')}</Chip>,
    },
    {
      key: 'auth',
      header: 'Auth',
      sortable: true,
      sortAccessor: (u) => u.auth_provider,
      render: (u) => <span className="font-mono text-caption text-slate">{u.auth_provider}</span>,
    },
    {
      key: 'active',
      header: 'Status',
      sortable: true,
      sortAccessor: (u) => (u.is_active ? 1 : 0),
      render: (u) => <StatusChip value={u.is_active ? 'active' : 'inactive'} />,
    },
    {
      key: 'last_login',
      header: 'Last login',
      align: 'right',
      sortable: true,
      sortAccessor: (u) => u.last_login_at ?? '',
      render: (u) =>
        u.last_login_at ? (
          <span className="text-caption text-slate" title={fmtTs(u.last_login_at)}>
            {relTime(u.last_login_at)}
          </span>
        ) : (
          <span className="text-slate-light">{DASH}</span>
        ),
    },
  ];

  // ---- entitlements ------------------------------------------------------
  const entRows = entitlements.data?.entitlements ?? [];
  const catalog = entitlements.data?.catalog;
  const entColumns: Column<DeskEntitlement>[] = [
    {
      key: 'dataset',
      header: 'Dataset',
      sortable: true,
      sortAccessor: (e) => e.dataset_code,
      render: (e) => <span className="font-mono text-caption text-navy">{e.dataset_code}</span>,
    },
    {
      key: 'tier',
      header: 'Tier',
      sortable: true,
      sortAccessor: (e) => e.tier ?? '',
      render: (e) => <span className="text-caption text-ink">{e.tier ?? DASH}</span>,
    },
    {
      key: 'status',
      header: 'Status',
      sortable: true,
      sortAccessor: (e) => e.status,
      render: (e) => <StatusChip value={e.status} />,
    },
    {
      key: 'from',
      header: 'From',
      align: 'right',
      sortable: true,
      sortAccessor: (e) => e.effective_from,
      render: (e) => <span className="text-caption text-slate">{fmtDate(e.effective_from)}</span>,
    },
  ];

  // ---- storage -----------------------------------------------------------
  const s = storage.data;
  const storageHasMetrics = Boolean(
    s && (s.provider || s.bucket || s.object_count != null || s.bytes != null || s.kms_key_state),
  );

  // ---- KPI row (all fields come from the OPEN tenant endpoint) ------------
  const fresh = t?.freshness ?? null;

  return (
    <div>
      <PageHeader
        breadcrumbs={[
          { label: 'Tenants', href: '/tenants' },
          { label: orgLabel ?? orgId },
        ]}
        title={orgLabel ?? 'Tenant'}
        subtitle={t?.organization_name}
        action={
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="sm"
              icon={<RefreshCw size={14} aria-hidden />}
              onClick={reloadAll}
            >
              Refresh
            </Button>
            <InspectTenantButton orgId={orgId} orgLabel={orgLabel} />
            <OpenBankDashboardButton orgId={orgId} orgLabel={orgLabel} />
          </div>
        }
      />

      {/* ---------------------------------------------------------- header */}
      <QueryBoundary
        loading={tenant.loading}
        error={tenant.error}
        onRetry={tenant.reload}
        context="Loading tenant"
        skeleton={<SkeletonRows rows={4} className="card" />}
      >
        {t && (
          <>
            <div className="card mb-5 p-5">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0 space-y-2">
                  <div className="flex flex-wrap items-center gap-x-5 gap-y-1">
                    {t.bank_id && (
                      <span className="text-caption text-slate">
                        Bank&nbsp;
                        <MonoId id={t.bank_id} />
                      </span>
                    )}
                    <span className="text-caption text-slate">
                      Org&nbsp;
                      <MonoId id={t.organization_id} />
                    </span>
                  </div>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <FreshnessChip summary={t.freshness} />
                  <SsoChip configured={t.sso_configured} enabled={t.sso_enabled} />
                  {t.storage_provider && <Chip mono>{t.storage_provider}</Chip>}
                </div>
              </div>

              <div className="mt-4 grid gap-x-10 border-t border-border-light pt-3 sm:grid-cols-2 lg:grid-cols-3">
                <FieldRow label="Jurisdiction">{t.jurisdiction_code ?? DASH}</FieldRow>
                <FieldRow label="Currency">
                  <span className="font-mono">{t.currency ?? DASH}</span>
                </FieldRow>
                <FieldRow label="License">{t.license_type ?? DASH}</FieldRow>
                <FieldRow label="Org created">
                  <span title={fmtTs(t.organization_created_at)}>
                    {fmtDate(t.organization_created_at)}
                  </span>
                </FieldRow>
                <FieldRow label="Bank created">
                  <span title={fmtTs(t.bank_created_at)}>{fmtDate(t.bank_created_at)}</span>
                </FieldRow>
                <FieldRow label="Last ingestion source">
                  {t.last_ingestion ? (
                    <>
                      <span className="font-mono">{t.last_ingestion.source_system}</span>
                      {' · '}
                      {fmtDate(t.last_ingestion.as_of_date)}
                    </>
                  ) : (
                    DASH
                  )}
                </FieldRow>
              </div>
            </div>

            {/* ------------------------------------------------------ KPIs */}
            <div className="mb-5 grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-5">
              <KpiStat
                label="Reporting periods"
                value={t.period_count}
                hint={t.latest_period_end ? `latest ${fmtDate(t.latest_period_end)}` : undefined}
              />
              <KpiStat label="Latest period" value={fmtDate(t.latest_period_end)} />
              <KpiStat
                label="Last ingestion"
                value={t.last_ingestion ? t.last_ingestion.status.replace(/_/g, ' ') : 'none'}
                status={kpiStatusFor(t.last_ingestion?.status)}
                hint={t.last_ingestion ? `as of ${fmtDate(t.last_ingestion.as_of_date)}` : undefined}
              />
              <KpiStat
                label="Live modules"
                value={fresh ? fresh.modules_reported : DASH}
                status={
                  fresh?.is_stale ? 'warn' : fresh && fresh.modules_reported > 0 ? 'ok' : undefined
                }
                hint={
                  fresh?.is_stale
                    ? `${fresh.stale_modules.length} stale`
                    : fresh?.latest_computed_at
                    ? `computed ${relTime(fresh.latest_computed_at)}`
                    : undefined
                }
              />
              <KpiStat
                label="SSO"
                value={t.sso_enabled ? 'enabled' : t.sso_configured ? 'configured' : 'none'}
                status={t.sso_enabled ? 'ok' : t.sso_configured ? 'warn' : undefined}
              />
            </div>
          </>
        )}
      </QueryBoundary>

      {/* ------------------------------------------------- deep data (gated) */}
      {sessionActive ? (
        <>
          {/* ------------------------------------------------ activity */}
          <SectionCard
            title="Activity"
            subtitle="Ingestions, jobs, official runs, packages, and audit events."
            noPadding
            className="mb-5"
            actions={
              activityHasMore ? (
                <Button
                  variant="secondary"
                  size="sm"
                  loading={activity.loading}
                  icon={<ChevronDown size={14} aria-hidden />}
                  onClick={() => setActivityLimit((l) => l + 50)}
                >
                  Load more
                </Button>
              ) : undefined
            }
          >
            <DeepSectionBody
              loading={activity.loading && !activity.data}
              error={activity.error}
              reload={activity.reload}
              context="Loading activity"
              orgId={orgId}
              orgLabel={orgLabel}
              showEmpty={activityItems.length === 0}
              empty={
                <EmptyState
                  title="No activity recorded"
                  description="Ingestions, jobs, official runs, packages, and audit events appear here as the tenant is used."
                />
              }
            >
              <DataTable
                columns={activityColumns}
                rows={activityItems}
                density="compact"
                initialSort={{ key: 'ts', dir: 'desc' }}
                getFilterText={(i) => [i.kind, i.summary, i.status].join(' ')}
                filterPlaceholder="Filter activity…"
                emptyMessage="No activity matches this filter."
              />
            </DeepSectionBody>
          </SectionCard>

          {/* ------------------------------------------------- metrics */}
          <TenantMetricsSection
            data={metrics.data}
            loading={metrics.loading}
            error={metrics.error}
            reload={metrics.reload}
            orgId={orgId}
            orgLabel={orgLabel}
          />

          {/* --------------------------------------- findings + ingestion */}
          <div className="mb-5 grid items-start gap-5 lg:grid-cols-2">
            <TenantFindingsSection
              data={findings.data}
              loading={findings.loading}
              error={findings.error}
              reload={findings.reload}
              orgId={orgId}
              orgLabel={orgLabel}
            />
            <TenantIngestionSection
              data={ingestion.data}
              loading={ingestion.loading}
              error={ingestion.error}
              reload={ingestion.reload}
              orgId={orgId}
              orgLabel={orgLabel}
            />
          </div>

          {/* ---------------------------------------- users + entitlements */}
          <div className="mb-5 grid items-start gap-5 lg:grid-cols-2">
            <SectionCard title="Users" subtitle={`${userRows.length} account(s)`} noPadding>
              <DeepSectionBody
                loading={users.loading && !users.data}
                error={users.error}
                reload={users.reload}
                context="Loading users"
                orgId={orgId}
                orgLabel={orgLabel}
                showEmpty={userRows.length === 0}
                empty={
                  <EmptyState
                    title="No users"
                    description="The tenant's own users (admins, analysts, viewers) appear here."
                  />
                }
              >
                <DataTable
                  columns={userColumns}
                  rows={userRows}
                  density="compact"
                  initialSort={{ key: 'email', dir: 'asc' }}
                  getFilterText={(u) =>
                    [u.email, u.full_name ?? '', u.role, u.auth_provider].join(' ')
                  }
                  filterPlaceholder="Filter users…"
                  pageSize={10}
                  emptyMessage="No users match this filter."
                />
              </DeepSectionBody>
            </SectionCard>

            <SectionCard
              title="Entitlements"
              subtitle={`${entRows.length} grant(s)`}
              noPadding
              footer={
                catalog ? (
                  <span className="text-caption text-slate">
                    Catalog:{' '}
                    {catalog.default_tier && (
                      <>
                        default <span className="font-medium text-navy">{catalog.default_tier}</span>
                        {catalog.datasets?.length ? ' · ' : ''}
                      </>
                    )}
                    {catalog.datasets?.length
                      ? `${catalog.datasets.length} dataset(s)`
                      : 'no catalog datasets reported'}
                  </span>
                ) : undefined
              }
            >
              <DeepSectionBody
                loading={entitlements.loading && !entitlements.data}
                error={entitlements.error}
                reload={entitlements.reload}
                context="Loading entitlements"
                orgId={orgId}
                orgLabel={orgLabel}
                showEmpty={entRows.length === 0}
                empty={
                  <EmptyState
                    title="No explicit grants"
                    description="With no rows, the tenant receives the catalog default tier automatically at publish and read time."
                  />
                }
              >
                <DataTable
                  columns={entColumns}
                  rows={entRows}
                  density="compact"
                  initialSort={{ key: 'dataset', dir: 'asc' }}
                  getFilterText={(e) => [e.dataset_code, e.tier ?? '', e.status].join(' ')}
                  filterPlaceholder="Filter datasets…"
                  pageSize={10}
                  emptyMessage="No datasets match this filter."
                />
              </DeepSectionBody>
            </SectionCard>
          </div>

          {/* ---------------------------------------- connections + storage */}
          <div className="mb-5 grid items-start gap-5 lg:grid-cols-2">
            <SectionCard
              title="Data-engine connections"
              subtitle={`${connections.length} connection(s)`}
              noPadding
            >
              <DeepSectionBody
                loading={engines.loading && !engines.data}
                error={engines.error}
                reload={engines.reload}
                context="Loading connections"
                orgId={orgId}
                orgLabel={orgLabel}
                showEmpty={connections.length === 0}
                empty={
                  <EmptyState
                    title="No connections for this org"
                    description="Market-data, Database-Direct, and core-banking (T24) connections appear here once the Data Engine is configured."
                  />
                }
              >
                <ConnectionsTable connections={connections} pageSize={10} />
              </DeepSectionBody>
            </SectionCard>

            <SectionCard title="Storage" subtitle="Object-store footprint and KMS state (best-effort).">
              <DeepSectionBody
                loading={storage.loading && !storage.data}
                error={storage.error}
                reload={storage.reload}
                context="Loading storage"
                orgId={orgId}
                orgLabel={orgLabel}
                showEmpty={false}
                empty={null}
              >
                {storageHasMetrics && s ? (
                  <div className="grid gap-x-10 sm:grid-cols-2">
                    <FieldRow label="Provider">{s.provider ?? DASH}</FieldRow>
                    <FieldRow label="Bucket">
                      <span className="font-mono text-caption">{s.bucket ?? DASH}</span>
                    </FieldRow>
                    <FieldRow label="Objects">
                      <span className="font-mono">
                        {s.object_count != null ? s.object_count.toLocaleString() : DASH}
                      </span>
                    </FieldRow>
                    <FieldRow label="Size">
                      <span className="font-mono">{formatBytes(s.bytes)}</span>
                    </FieldRow>
                    <FieldRow label="KMS key state">
                      {s.kms_key_state ? <StatusChip value={s.kms_key_state} /> : DASH}
                    </FieldRow>
                  </div>
                ) : (
                  <EmptyState
                    title="No storage metrics"
                    description={
                      s?.note ?? 'The operator API returned no object-store metrics for this tenant.'
                    }
                  />
                )}
                {storageHasMetrics && s?.note && (
                  <p className="mt-3 border-t border-border-light pt-3 text-caption text-slate">
                    {s.note}
                  </p>
                )}
              </DeepSectionBody>
            </SectionCard>
          </div>

          {/* --------------------------------------------------- config */}
          <TenantConfigSection
            data={config.data}
            loading={config.loading}
            error={config.error}
            reload={config.reload}
            orgId={orgId}
            orgLabel={orgLabel}
          />

          {/* ---------------------------------------- remediation (fix) */}
          <RemediationPanel
            orgId={orgId}
            orgLabel={orgLabel}
            ingestionBatches={ingestion.data?.batches ?? []}
            config={config.data}
            onRecomputed={() => {
              metrics.reload();
              findings.reload();
            }}
            onOfficialRun={() => {
              metrics.reload();
              activity.reload();
            }}
            onReran={() => {
              ingestion.reload();
              activity.reload();
            }}
            onConfigChanged={() => config.reload()}
          />
        </>
      ) : (
        <GatedTenantData orgId={orgId} orgLabel={orgLabel} />
      )}
    </div>
  );
}
