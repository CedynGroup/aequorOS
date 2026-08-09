'use client';

/**
 * Shared chrome for every FTP workspace tab: page header with breadcrumbs,
 * freshness / run badges, the run-all-scenarios action, the not-yet-stored
 * banner, and the query boundary. Sub-pages receive the loaded dashboard via
 * a render prop so the payload is fetched (and cached) once per query key.
 */

import type { ReactNode } from 'react';
import type {
  FtpDashboardRead,
  FtpMetricsRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useFtpDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

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

  const data = dashboard.data;


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
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && (
          <div className="px-8 py-6 space-y-6">
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
