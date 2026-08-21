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
import Link from 'next/link';
import { ArrowRight, BellRing, Info } from 'lucide-react';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import SectionCard from '@/components/ui/SectionCard';
import QueryBoundary from '@/components/ui/QueryBoundary';
import EmptyState from '@/components/ui/EmptyState';
import { useBankContext } from '@/components/shell/BankContext';
import { useBankAlerts } from '@/lib/api/hooks';
import {
  useSdiCapitalChecks,
  useSdiCapitalSummary,
  useSdiLargeExposures,
  useSdiLiquidityPosition,
} from '@/components/basel/sdiHooks';
import AlertStream, { type AlertGroupBy } from '@/components/alerts/AlertStream';

const ALERTS_LIMIT = 200; // backend maximum per read

export default function AlertCenterPage() {
  const { bank, moduleScope } = useBankContext();
  const alerts = useBankAlerts(bank?.id, ALERTS_LIMIT);
  const isSdi = moduleScope.institutionClass === 'sdi';
  const sdiLiquidity = useSdiLiquidityPosition(isSdi ? bank?.id : undefined);
  const sdiCapital = useSdiCapitalSummary(isSdi ? bank?.id : undefined);
  const sdiChecks = useSdiCapitalChecks(isSdi ? bank?.id : undefined);
  const sdiExposures = useSdiLargeExposures(isSdi ? bank?.id : undefined);
  const [groupBy, setGroupBy] = useState<AlertGroupBy>('module');

  const data = alerts.data;
  const critical = data?.bySeverity?.critical ?? 0;
  const high = data?.bySeverity?.high ?? 0;
  const modulesAffected = Object.keys(data?.byModule ?? {}).length;
  const sdiSignals = isSdi
    ? [
        ...(sdiLiquidity.data?.ratios ?? [])
          .filter((row) => row.status === 'below_minimum')
          .map((row) => ({ label: row.label, detail: 'LMTD Table 1 ratio below its floor' })),
        ...(sdiLiquidity.data?.reserves ?? [])
          .filter((row) => row.status === 'below_minimum')
          .map((row) => ({ label: row.label, detail: 'Liquidity reserve below its floor' })),
        ...(sdiChecks.data?.checks ?? [])
          .filter((row) => row.compliant === false)
          .map((row) => ({ label: row.check.replaceAll('_', ' '), detail: row.detail })),
        ...(sdiExposures.data?.exposures ?? [])
          .filter((row) => row.status === 'above_limit')
          .map((row) => ({ label: row.counterparty_name, detail: 'Large exposure above its applicable limit' })),
        ...(sdiCapital.data?.status === 'red'
          ? [{ label: 'Capital adequacy ratio', detail: 'Section 29 capital adequacy is below its floor' }]
          : []),
      ]
    : [];

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

            {isSdi && (
              <SectionCard
                title="Current SDI control signals"
                subtitle="Read directly from the SDI liquidity, capital, and large-exposure controls while persistent pipeline alerts are reconciled."
              >
                {sdiSignals.length === 0 ? (
                  <p className="text-body text-slate">No current SDI control breach is reported by the available diagnostic data.</p>
                ) : (
                  <ul className="space-y-3">
                    {sdiSignals.map((signal) => (
                      <li key={`${signal.label}-${signal.detail}`} className="border-l-2 border-critical pl-3">
                        <p className="text-body font-medium text-navy capitalize">{signal.label}</p>
                        <p className="mt-1 text-caption text-slate">{signal.detail}</p>
                      </li>
                    ))}
                  </ul>
                )}
              </SectionCard>
            )}

            {data.items.length === 0 ? (
              <EmptyState
                Icon={BellRing}
                title="No open alerts"
                description="Every live module is inside its limits for the current period. New breaches appear here automatically as the pipeline recomputes on ingestion."
                action={
                  <Link
                    href="/risk"
                    className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                  >
                    View Risk & Limits
                    <ArrowRight size={13} aria-hidden />
                  </Link>
                }
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
