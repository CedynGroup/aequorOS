'use client';

/**
 * Shared chrome for every FX workspace tab: page header with breadcrumbs,
 * freshness / run badges, the run-all-scenarios action, the not-yet-stored
 * banner, and the query boundary. Sub-pages receive the loaded dashboard via
 * a render prop so the payload is fetched (and cached) once per query key.
 */

import type { ReactNode } from 'react';
import type {
  FxDashboardRead,
  FxMetricsRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import LiveEngineNote from '@/components/live/LiveEngineNote';
import { useFxDashboard, useRegulatoryRun } from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

export type FxFrameContext = {
  data: FxDashboardRead;
  metrics: FxMetricsRead;
  /** Latest stored baseline run (audit trail + parameter snapshot), if any. */
  run: RegulatoryRunRead | undefined;
  bankId: string | undefined;
  periodId: string | undefined;
};

export default function FxModuleFrame({
  crumb,
  title,
  subtitle,
  children,
}: {
  /** Trailing breadcrumb / active tab label. */
  crumb: string;
  title: string;
  subtitle?: string;
  children: (ctx: FxFrameContext) => ReactNode;
}) {
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const dashboard = useFxDashboard(bankId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);

  const data = dashboard.data;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'FX Risk' },
          { label: crumb },
        ]}
        title={title}
        subtitle={subtitle}
        action={data ? <LiveEngineNote live={data.live} stored={data.stored} /> : undefined}
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
              periodId: data.period.id,
            })}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
