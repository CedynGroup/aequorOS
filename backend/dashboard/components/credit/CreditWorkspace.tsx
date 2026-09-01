'use client';

/**
 * Shared frame for the Credit / Loan Book tabs: page header with the live
 * provenance note, query boundary, and the dashboard payload handed to the tab
 * body via render prop — the IRRBB workspace pattern, so each tab stays purely
 * presentational.
 */

import type { ReactNode } from 'react';
import type { CreditDashboardRead, CreditMetricsRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import LiveEngineNote from '@/components/live/LiveEngineNote';
import { useCreditDashboard } from '@/lib/api/hooks';

export type CreditTabContext = {
  data: CreditDashboardRead;
  metrics: CreditMetricsRead;
  bankId: string | undefined;
  periodId: string | undefined;
};

export default function CreditWorkspace({
  crumb,
  subtitle,
  children,
}: {
  crumb: string;
  subtitle: string;
  children: (ctx: CreditTabContext) => ReactNode;
}) {
  const { bank } = useBankContext();
  const bankId = bank?.id;
  const dashboard = useCreditDashboard(bankId);
  const data = dashboard.data;

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Credit', href: '/credit' },
          { label: crumb },
        ]}
        title="Credit"
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
              bankId,
              periodId: data.period.id,
            })}
          </div>
        )}
      </QueryBoundary>
    </>
  );
}
