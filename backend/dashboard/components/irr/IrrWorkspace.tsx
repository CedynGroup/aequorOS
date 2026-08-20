'use client';

/**
 * Shared frame for every IRRBB workspace tab: page header (freshness badge,
 * latest-run audit chip, run-all action), query boundary, and the dashboard
 * payload handed to the tab body via render prop. Keeps the five tabs'
 * data wiring identical so each page is purely presentational.
 */

import type { ReactNode } from 'react';
import type {
  IrrDashboardRead,
  IrrMetricsRead,
  RegulatoryRunRead,
} from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import LiveEngineNote from '@/components/live/LiveEngineNote';
import {
  useIrrDashboard,
  useRegulatoryRun,
} from '@/lib/api/hooks';
import { fmtDateUTC } from '@/lib/api/values';

export type IrrTabContext = {
  data: IrrDashboardRead;
  metrics: IrrMetricsRead;
  /** Latest stored regulatory run backing this dashboard, when available. */
  latestRun: RegulatoryRunRead | undefined;
  /** Timestamp for SectionCard footers: live recompute, else stored run. */
  computedAt: Date | undefined;
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
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const dashboard = useIrrDashboard(bankId);
  const latestRun = useRegulatoryRun(bankId, dashboard.data?.latestRunId);

  const data = dashboard.data;
  const m = data?.metrics;


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
        action={data ? <LiveEngineNote live={data.live} stored={data.stored} /> : undefined}
      />

      <QueryBoundary
        isLoading={dashboard.isLoading}
        error={dashboard.error}
        onRetry={() => dashboard.refetch()}
      >
        {data && m && (
          <div className="px-8 py-6 space-y-6">
            {children({
              data,
              metrics: m,
              latestRun: latestRun.data,
              computedAt: data.live?.computedAt ?? latestRun.data?.createdAt,
              bankId,
              periodId: data.period.id,
            })}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
