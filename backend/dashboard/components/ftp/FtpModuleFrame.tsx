'use client';

/**
 * Shared chrome for every FTP workspace tab: page header with breadcrumbs,
 * freshness / run badges, the run-all-scenarios action, the not-yet-stored
 * banner, and the query boundary. Sub-pages receive the loaded dashboard via
 * a render prop so the payload is fetched (and cached) once per query key.
 */

import type { ReactNode } from 'react';
import { Info, Loader2, Zap } from 'lucide-react';
import type {
  FtpDashboardRead,
  FtpMetricsRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import RunBadge from '@/components/ui/RunBadge';
import QueryBoundary from '@/components/ui/QueryBoundary';
import FreshnessBadge from '@/components/live/FreshnessBadge';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useFtpDashboard,
  useRegulatoryRun,
  useRunAllFtpScenarios,
} from '@/lib/api/hooks';
import { fmtDateUTC, isoDate } from '@/lib/api/values';

export type FtpFrameContext = {
  data: FtpDashboardRead;
  metrics: FtpMetricsRead;
  /** Latest stored baseline run (audit trail + parameter snapshot), if any. */
  run: RegulatoryRunRead | undefined;
  bankId: string | undefined;
  periodId: string | undefined;
};

export default function FtpModuleFrame({
  crumb,
  title,
  subtitle,
  children,
}: {
  /** Trailing breadcrumb / active tab label. */
  crumb: string;
  title: string;
  subtitle?: string;
  children: (ctx: FtpFrameContext) => ReactNode;
}) {
  const { bank, period } = useBankContext();
  const bankId = bank?.id;
  const periodId = period?.id;

  const dashboard = useFtpDashboard(bankId, periodId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);
  const runAll = useRunAllFtpScenarios(bankId);

  const data = dashboard.data;

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
          { label: 'Funds Transfer Pricing' },
          { label: crumb },
        ]}
        title={title}
        subtitle={subtitle}
        asOf={period ? fmtDateUTC(period.periodEnd) : undefined}
        action={
          <div className="flex items-center gap-2">
            <FreshnessBadge
              bankId={bankId}
              periodId={periodId}
              module="ftp"
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
        {data && (
          <div className="px-8 py-6 space-y-6">
            {!data.stored && (
              <div className="card border-l-4 border-l-warning bg-warning-light/40 px-5 py-3.5 flex items-start gap-3">
                <Info
                  size={16}
                  className="text-warning shrink-0 mt-0.5"
                  aria-hidden
                />
                <p className="text-body text-navy/85 leading-relaxed">
                  Showing a live computation for this period — run all
                  scenarios to persist auditable regulatory runs (and the
                  parameter snapshot) for the rate and funding-stress
                  overlays.
                </p>
              </div>
            )}
            {children({
              data,
              metrics: data.metrics,
              run: latestRun.data,
              bankId,
              periodId,
            })}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
