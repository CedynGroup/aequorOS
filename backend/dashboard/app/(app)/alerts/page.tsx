'use client';

/**
 * Alert Center — every open limit-breach finding across the live modules,
 * grouped by module or severity. The live-findings API exposes only open
 * critical/high findings for the current period (cleared breaches are
 * superseded server-side with no read endpoint, and no acknowledge mutation
 * exists), so this page renders the open stream only — no resolved tab, no
 * dead acknowledge buttons.
 */

import { useState } from 'react';
import { BellRing, Info } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { useBankContext } from '@/components/shell/BankContext';
import { useBankAlerts } from '@/lib/api/hooks';
import AlertStream, { type AlertGroupBy } from '@/components/alerts/AlertStream';

const ALERTS_LIMIT = 200; // backend maximum per read

export default function AlertCenterPage() {
  const { bank } = useBankContext();
  const alerts = useBankAlerts(bank?.id, ALERTS_LIMIT);
  const [groupBy, setGroupBy] = useState<AlertGroupBy>('module');

  const data = alerts.data;
  const critical = data?.bySeverity?.critical ?? 0;
  const high = data?.bySeverity?.high ?? 0;
  const modulesAffected = Object.keys(data?.byModule ?? {}).length;

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Command' }, { label: 'Alerts' }]}
        title="Alert Center"
        subtitle="Open limit breaches across every live module, reconciled by the pipeline on each refresh — breaches clear automatically when the data does."
        action={
          <div
            className="inline-flex rounded-md border border-border overflow-hidden"
            role="group"
            aria-label="Group alerts by"
          >
            {(['module', 'severity'] as const).map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => setGroupBy(option)}
                className={`px-3 py-1.5 text-caption font-medium transition-colors ${
                  groupBy === option
                    ? 'bg-action-light text-action'
                    : 'bg-surface-raised text-slate hover:text-navy'
                }`}
              >
                By {option}
              </button>
            ))}
          </div>
        }
      />

      <QueryBoundary
        isLoading={alerts.isLoading}
        error={alerts.error}
        onRetry={() => alerts.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
              <KpiStat
                label="Open alerts"
                value={data.total}
                status={data.total > 0 ? 'crit' : 'ok'}
                hint="current reporting period"
              />
              <KpiStat
                label="Critical"
                value={critical}
                status={critical > 0 ? 'crit' : 'ok'}
                hint="hard limit breaches"
              />
              <KpiStat
                label="High"
                value={high}
                status={high > 0 ? 'warn' : 'ok'}
                hint="early warnings"
              />
              <KpiStat
                label="Modules affected"
                value={modulesAffected}
                status={modulesAffected > 0 ? 'warn' : 'ok'}
                hint="modules with open findings"
              />
            </div>

            {data.items.length === 0 ? (
              <EmptyState
                Icon={BellRing}
                title="No open alerts"
                description="Every live module is inside its limits for the current period. New breaches appear here automatically as the pipeline recomputes on ingestion."
              />
            ) : (
              <>
                <AlertStream items={data.items} groupBy={groupBy} />
                {data.total > data.items.length && (
                  <p className="text-caption text-slate">
                    Showing the first {data.items.length} of {data.total} open alerts
                    (API page limit).
                  </p>
                )}
              </>
            )}

            <div className="card px-5 py-3.5 flex items-start gap-3">
              <Info size={15} className="text-slate shrink-0 mt-0.5" aria-hidden />
              <p className="text-caption text-slate leading-relaxed">
                The live-findings API serves open critical/high findings for the
                latest reporting period. Cleared breaches are superseded by the
                pipeline and are not exposed by any endpoint, so no resolved
                history is shown; findings clear automatically — there is no
                acknowledge action.
              </p>
            </div>
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
