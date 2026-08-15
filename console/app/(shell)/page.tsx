'use client';

import { useEffect, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { Button, PageHeader, QueryBoundary, SkeletonCard } from '@/components/ui';
import { getOverview, listTenants } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtTimestamp } from '@/lib/format';
import { ConnectionsRollup } from '@/components/home/ConnectionsRollup';
import { DeskStatus } from '@/components/home/DeskStatus';
import { FleetSnapshot } from '@/components/home/FleetSnapshot';
import { NeedsAttention } from '@/components/home/NeedsAttention';
import { OverviewKpis } from '@/components/home/OverviewKpis';

/**
 * Operator home cockpit — the fleet-overview control room.
 *
 * Primary source is a SINGLE `getOverview()` rollup (KPI row, needs-attention
 * queue, desk approvals, connection health); the fleet table adds one
 * `listTenants()` call. There is deliberately no per-tenant fan-out on this
 * page. Both fetches share the header's Refresh control and the `As of` stamp.
 */
export default function HomePage() {
  const overview = useApi(getOverview);
  const tenants = useApi(listTenants);

  // Set the as-of stamp after mount to avoid an SSR/CSR hydration mismatch.
  const [asOf, setAsOf] = useState('');
  useEffect(() => {
    setAsOf(fmtTimestamp(new Date()));
  }, []);

  const refresh = () => {
    overview.reload();
    tenants.reload();
    setAsOf(fmtTimestamp(new Date()));
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Overview"
        subtitle="Cross-tenant operator cockpit — tenant health, ingestion, jobs, connections, and the desk approvals queue."
        asOf={asOf || undefined}
        action={
          <Button
            variant="secondary"
            size="sm"
            icon={<RefreshCw size={14} />}
            onClick={refresh}
            loading={overview.loading || tenants.loading}
          >
            Refresh
          </Button>
        }
      />

      <QueryBoundary
        loading={overview.loading}
        error={overview.error}
        onRetry={overview.reload}
        context="Loading cockpit"
        skeleton={<CockpitSkeleton />}
      >
        {overview.data && (
          <div className="space-y-6">
            <OverviewKpis overview={overview.data} />

            <div className="grid gap-6 lg:grid-cols-3">
              <div className="lg:col-span-2">
                <NeedsAttention items={overview.data.needs_attention} />
              </div>
              <div className="space-y-6">
                <DeskStatus desk={overview.data.desk} />
                <ConnectionsRollup connections={overview.data.connections} />
              </div>
            </div>
          </div>
        )}
      </QueryBoundary>

      <FleetSnapshot
        tenants={tenants.data?.tenants ?? null}
        loading={tenants.loading}
        error={tenants.error}
        onRetry={tenants.reload}
      />
    </div>
  );
}

/** Lean cockpit placeholder: the KPI row plus the two-column body. */
function CockpitSkeleton() {
  return (
    <div className="space-y-6" aria-busy="true" aria-label="Loading cockpit">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <div className="card h-64 lg:col-span-2" />
        <div className="card h-64" />
      </div>
    </div>
  );
}
