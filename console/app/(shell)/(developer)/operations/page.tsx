'use client';

import Link from 'next/link';
import { Info } from 'lucide-react';
import { listDataEngines, listTenants, type DataEngineConnection } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtTs, relTime } from '@/lib/format';
import {
  EmptyState,
  ErrorPanel,
  PageHeader,
  SkeletonRows,
  StatusChip,
} from '@/components/ui';

/**
 * /operations — the cross-tenant data-engine connection wall.
 * Sources:
 *  - GET /operator/v1/data-engines (the wall itself)
 *  - GET /operator/v1/tenants (only to resolve org ids to names for grouping;
 *    an unknown org id is shown as the raw OR- id, never dropped)
 */
export default function OperationsPage() {
  const engines = useApi(listDataEngines);
  const tenants = useApi(listTenants);

  const orgNames = new Map<string, string>();
  for (const t of tenants.data?.tenants ?? []) {
    orgNames.set(t.organization_id, t.organization_name);
  }

  // Group connections by organization, preserving API order within a group.
  const groups = new Map<string, DataEngineConnection[]>();
  for (const c of engines.data?.connections ?? []) {
    const list = groups.get(c.organization_id);
    if (list) list.push(c);
    else groups.set(c.organization_id, [c]);
  }

  return (
    <div>
      <PageHeader
        title="Operations"
        sub="Data-engine connections across every tenant on this deployment."
      />

      <div className="space-y-6">
        {engines.loading && (
          <div className="card">
            <SkeletonRows rows={6} />
          </div>
        )}
        {engines.error && (
          <ErrorPanel error={engines.error} onRetry={engines.reload} context="Loading connections" />
        )}

        {engines.data && groups.size === 0 && (
          <div className="card">
            <EmptyState
              title="No data-engine connections anywhere yet"
              hint="Connections appear once a tenant configures market data, Database-Direct, or a core-banking adapter in their Data Engine."
            />
          </div>
        )}

        {engines.data &&
          Array.from(groups.entries()).map(([orgId, connections]) => (
            <section key={orgId} className="card">
              <div className="flex items-baseline justify-between gap-3 border-b border-border-light px-5 py-3">
                <h2 className="min-w-0 truncate text-h3 text-navy">
                  {orgNames.get(orgId) ?? 'Unknown organization'}
                </h2>
                <Link
                  href={`/tenants/${orgId}`}
                  className="shrink-0 font-mono text-caption text-slate hover:text-action"
                >
                  {orgId}
                </Link>
              </div>
              <ul className="divide-y divide-border-light">
                {connections.map((c, i) => (
                  <li key={`${c.display_name}-${i}`} className="flex items-center gap-4 px-5 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-body font-medium text-navy">
                        {c.display_name}
                      </div>
                      <div className="text-caption text-slate">
                        <span className="font-mono">
                          {c.engine} · {c.system}
                        </span>
                        {c.credential_expires_at && (
                          <span className="text-warning">
                            {' '}
                            · credential expires {relTime(c.credential_expires_at)}
                          </span>
                        )}
                      </div>
                    </div>
                    <span
                      className="shrink-0 text-caption text-slate"
                      title={fmtTs(c.last_activity_at)}
                    >
                      last activity {relTime(c.last_activity_at)}
                      {c.last_activity_status ? ` (${c.last_activity_status})` : ''}
                    </span>
                    <StatusChip value={c.status} />
                  </li>
                ))}
              </ul>
            </section>
          ))}

        {/* Honest scope note — what this screen does NOT show yet. */}
        <div className="card flex items-start gap-3 p-4">
          <Info size={16} className="mt-0.5 shrink-0 text-action" />
          <div className="text-caption text-slate">
            <p className="font-medium text-navy">Not surfaced here yet</p>
            <ul className="mt-1 list-disc space-y-0.5 pl-5">
              <li>
                Jobs board (queued / running / failed background jobs) — pending the job-claimant
                column on the backend, so cross-worker attribution stays honest.
              </li>
              <li>
                Per-connection sync history — the operator API exposes only the latest activity
                per connection today.
              </li>
            </ul>
            <p className="mt-1">Nothing on this screen is simulated; if it isn&apos;t listed, we don&apos;t show it.</p>
          </div>
        </div>
      </div>
    </div>
  );
}
