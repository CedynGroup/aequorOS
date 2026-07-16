'use client';

/**
 * Shared frame for every IRRBB workspace tab: page header (freshness badge,
 * latest-run audit chip, run-all action), query boundary, and the dashboard
 * payload handed to the tab body via render prop. Keeps the five tabs'
 * data wiring identical so each page is purely presentational.
 */

import type { ReactNode } from 'react';
import { Info, Loader2, Zap } from 'lucide-react';
import type {
  IrrDashboardRead,
  IrrMetricsRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary from '@/components/ui/QueryBoundary';
import RunBadge from '@/components/ui/RunBadge';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useIrrDashboard,
  useRegulatoryRun,
  useRunAllIrrScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate } from '@/lib/api/values';

export type IrrTabContext = {
  data: IrrDashboardRead;
  metrics: IrrMetricsRead;
  /** Latest stored regulatory run backing this dashboard, when available. */
  latestRun: RegulatoryRunRead | undefined;
  /** Timestamp for SectionCard footers: live recompute, else stored run. */
  computedAt: Date | undefined;
  runAllButton: ReactNode;
  bankId: string | undefined;
  periodId: string | undefined;
};

export default function IrrWorkspace({
  crumb,
  subtitle,
  children,
}: {
  /** Trailing breadcrumb for the active tab, e.g. "Gap Analysis". */
  crumb: string;
  subtitle: string;
  children: (ctx: IrrTabContext) => ReactNode;
}) {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useIrrDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runAll = useRunAllIrrScenarios(bankId);

  const data = dashboard.data;
  const m = data?.metrics;

  const runAllButton = (
    <button
      type="button"
      disabled={runAll.isPending || !periodId}
      onClick={() => periodId && runAll.mutate({ reportingPeriodId: periodId })}
      className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
    >
      {runAll.isPending ? (
        <Loader2 size={13} className="animate-spin" aria-hidden />
      ) : (
        <Zap size={13} aria-hidden />
      )}
      Run all scenarios
    </button>
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Interest Rate Risk', href: '/irr' },
          { label: crumb },
        ]}
        title="Interest Rate Risk"
        subtitle={subtitle}
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="irr"
              asOfDate={period ? isoDate(period.periodEnd) : undefined}
            />
            {latestRun.data && <RunBadge run={latestRun.data} />}
            {runAllButton}
          </div>
        }
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && m && (
          <div className="px-8 py-6 space-y-6">
            {!data.stored && (
              <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-3.5 flex items-start gap-3">
                <Info size={16} className="text-warning shrink-0 mt-0.5" aria-hidden />
                <p className="text-body text-navy/85 leading-relaxed">
                  Showing a live computation for this period — run all
                  scenarios to persist auditable regulatory runs for the six
                  Basel IRRBB shocks.
                </p>
              </div>
            )}
            {children({
              data,
              metrics: m,
              latestRun: latestRun.data,
              computedAt: data.live?.computedAt ?? latestRun.data?.createdAt,
              runAllButton,
              bankId,
              periodId,
            })}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
