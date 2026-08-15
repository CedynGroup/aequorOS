'use client';

import { useMemo, useState } from 'react';
import { Download, Info } from 'lucide-react';
import { getOverview, listDataEngines, listOperatorJobs, listTenants } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import {
  Button,
  EmptyState,
  ErrorPanel,
  Field,
  KpiStat,
  PageHeader,
  QueryBoundary,
  SectionCard,
  Select,
  SkeletonRows,
} from '@/components/ui';
import { ConnectionsTable } from '@/components/tenants/ConnectionsTable';
import { JobsTable } from '@/components/tenants/JobsTable';
import { credentialHealth, csvFilename, downloadCsv } from '@/components/tenants/util';

/**
 * /operations — the cross-tenant data-engine operations board.
 * Sources:
 *  - GET /operator/v1/data-engines   (the connection wall)
 *  - GET /operator/v1/tenants        (resolve org ids to names)
 *  - GET /operator/v1/overview       (connection-health + fleet signals rollup)
 *
 * The overview rollup is best-effort: if it is unavailable the connection table
 * still renders — no fabricated health numbers.
 */

const ALL = '__all__';

export default function OperationsPage() {
  const engines = useApi(listDataEngines);
  const tenants = useApi(listTenants);
  const overview = useApi(getOverview);
  const jobs = useApi(listOperatorJobs);

  const [orgFilter, setOrgFilter] = useState(ALL);
  const [engineFilter, setEngineFilter] = useState(ALL);
  const [statusFilter, setStatusFilter] = useState(ALL);

  const connections = engines.data?.connections ?? [];

  const orgNames = useMemo(() => {
    const map = new Map<string, string>();
    for (const t of tenants.data?.tenants ?? []) map.set(t.organization_id, t.organization_name);
    return map;
  }, [tenants.data]);
  const orgNameFor = (id: string) => orgNames.get(id);

  // Distinct option lists, drawn straight from the payload.
  const orgOptions = useMemo(
    () => Array.from(new Set(connections.map((c) => c.organization_id))).sort(),
    [connections],
  );
  const engineOptions = useMemo(
    () => Array.from(new Set(connections.map((c) => c.engine))).sort(),
    [connections],
  );
  const statusOptions = useMemo(
    () => Array.from(new Set(connections.map((c) => c.status))).sort(),
    [connections],
  );

  const filtered = useMemo(
    () =>
      connections.filter(
        (c) =>
          (orgFilter === ALL || c.organization_id === orgFilter) &&
          (engineFilter === ALL || c.engine === engineFilter) &&
          (statusFilter === ALL || c.status === statusFilter),
      ),
    [connections, orgFilter, engineFilter, statusFilter],
  );

  // Local credential-expiry rollup (independent of the overview endpoint).
  const expiring = useMemo(
    () =>
      connections.filter((c) => {
        const h = credentialHealth(c.credential_expires_at);
        return h != null && (h.expired || h.days <= 30);
      }).length,
    [connections],
  );

  function exportCsv() {
    downloadCsv(
      csvFilename('connections'),
      [
        'organization',
        'organization_id',
        'bank_id',
        'engine',
        'system',
        'display_name',
        'status',
        'last_activity_at',
        'last_activity_status',
        'credential_expires_at',
      ],
      filtered.map((c) => [
        orgNameFor(c.organization_id) ?? '',
        c.organization_id,
        c.bank_id,
        c.engine,
        c.system,
        c.display_name,
        c.status,
        c.last_activity_at ?? '',
        c.last_activity_status ?? '',
        c.credential_expires_at ?? '',
      ]),
    );
  }

  const ov = overview.data;

  return (
    <div>
      <PageHeader
        title="Operations"
        subtitle="Data-engine connections and fleet signals across every tenant on this deployment."
        action={
          <Button
            variant="secondary"
            size="sm"
            icon={<Download size={14} aria-hidden />}
            onClick={exportCsv}
            disabled={filtered.length === 0}
          >
            Export CSV
          </Button>
        }
      />

      {/* -------------------------------------------- connection-health rollup */}
      {ov && (
        <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiStat label="Connections" value={connections.length} hint="across all tenants" />
          <KpiStat label="Healthy" value={ov.connections.ok} status="ok" />
          <KpiStat
            label="Warnings"
            value={ov.connections.warn}
            status={ov.connections.warn > 0 ? 'warn' : undefined}
          />
          <KpiStat
            label="Critical"
            value={ov.connections.crit}
            status={ov.connections.crit > 0 ? 'crit' : 'ok'}
          />
        </div>
      )}

      {ov && (
        <div className="mb-5 grid grid-cols-2 gap-4 lg:grid-cols-4">
          <KpiStat label="Credentials expiring" value={expiring} status={expiring > 0 ? 'warn' : 'ok'} hint="≤ 30 days or expired" />
          <KpiStat label="Jobs running" value={ov.jobs.running} />
          <KpiStat
            label="Jobs failed (24h)"
            value={ov.jobs.failed_24h}
            status={ov.jobs.failed_24h > 0 ? 'crit' : 'ok'}
          />
          <KpiStat
            label="Ingestion failed (24h)"
            value={ov.ingestion.failed_24h}
            status={ov.ingestion.failed_24h > 0 ? 'crit' : 'ok'}
          />
        </div>
      )}

      {/* -------------------------------------------------- connection wall */}
      <SectionCard
        title="Connections"
        subtitle={`${filtered.length} of ${connections.length} shown`}
        noPadding
        className="mb-5"
        actions={
          connections.length > 0 ? (
            <div className="flex flex-wrap items-end gap-2">
              <Field label="Org" className="w-40">
                <Select value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
                  <option value={ALL}>All orgs</option>
                  {orgOptions.map((id) => (
                    <option key={id} value={id}>
                      {orgNameFor(id) ?? id}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Engine" className="w-36">
                <Select value={engineFilter} onChange={(e) => setEngineFilter(e.target.value)}>
                  <option value={ALL}>All engines</option>
                  {engineOptions.map((e) => (
                    <option key={e} value={e}>
                      {e}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Status" className="w-36">
                <Select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
                  <option value={ALL}>All statuses</option>
                  {statusOptions.map((s) => (
                    <option key={s} value={s}>
                      {s}
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
          ) : undefined
        }
      >
        <QueryBoundary
          loading={engines.loading}
          error={engines.error}
          onRetry={engines.reload}
          context="Loading connections"
          skeleton={<SkeletonRows rows={6} />}
        >
          {connections.length === 0 ? (
            <EmptyState
              title="No data-engine connections anywhere yet"
              description="Connections appear once a tenant configures market data, Database-Direct, or a core-banking adapter in their Data Engine."
            />
          ) : (
            <ConnectionsTable
              connections={filtered}
              orgNameFor={orgNameFor}
              showOrg
              pageSize={30}
              filterPlaceholder="Search org, connection, system…"
              emptyMessage="No connections match these filters."
            />
          )}
        </QueryBoundary>
      </SectionCard>

      <SectionCard
        title="Jobs"
        subtitle="Cross-tenant queue history with the runtime that claimed each job."
        noPadding
        className="mb-5"
      >
        <QueryBoundary
          loading={jobs.loading}
          error={jobs.error}
          onRetry={jobs.reload}
          context="Loading jobs"
          skeleton={<SkeletonRows rows={6} />}
        >
          <JobsTable jobs={jobs.data?.jobs ?? []} orgNameFor={orgNameFor} />
        </QueryBoundary>
      </SectionCard>

      {/* -------------------------------------------------- honest scope note */}
      {overview.error && (
        <div className="mb-5">
          <ErrorPanel
            error={overview.error}
            onRetry={overview.reload}
            context="Loading fleet signals"
          />
        </div>
      )}

      <div className="card flex items-start gap-3 p-4">
        <Info size={16} className="mt-0.5 shrink-0 text-action" />
        <div className="text-caption text-slate">
          <p className="font-medium text-navy">Not surfaced here yet</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            <li>
              Per-connection sync history — the operator API exposes only the latest activity per
              connection today.
            </li>
          </ul>
          <p className="mt-1">
            Nothing on this screen is simulated; if it isn&apos;t listed, we don&apos;t show it.
          </p>
        </div>
      </div>
    </div>
  );
}
