'use client';

import Link from 'next/link';
import { LineChart as LineChartIcon } from 'lucide-react';
import type { FxCurrencyPositionRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import ChartFrame from '@/components/ui/ChartFrame';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import FxModuleFrame, { type FxFrameContext } from '@/components/fx/FxModuleFrame';
import ForwardCurve, { type ForwardPoint } from '@/components/fx/charts/ForwardCurve';
import { num } from '@/lib/api/values';

export default function FxForwardsPage() {
  return (
    <FxModuleFrame
      crumb="Forwards"
      title="FX Forwards"
      subtitle="Forward curve monitor · outright points by tenor against the period-end spot"
    >
      {(ctx) => <ForwardsBody ctx={ctx} />}
    </FxModuleFrame>
  );
}

function ForwardsBody({ ctx }: { ctx: FxFrameContext }) {
  const { data } = ctx;

  /**
   * The canonical market-data store currently carries spot fixes only
   * (CanonicalFxRate has no tenor dimension), so no forward points can
   * exist yet. The monitor below renders as soon as forward-tenor rows are
   * ingested; until then the page shows a first-class empty state plus the
   * real spot references the NOP engine revalued this period with.
   */
  const forwardPoints: ForwardPoint[] = [];

  const spotColumns: Column<FxCurrencyPositionRead>[] = [
    { key: 'ccy', header: 'Pair', render: (r) => `${r.currency}/GHS`, width: '24%' },
    {
      key: 'spot',
      header: 'Period-end spot (GHS)',
      numeric: true,
      render: (r) => num(r.spotGhs).toFixed(4),
    },
    {
      key: 'side',
      header: 'Bank position',
      align: 'right',
      render: (r) => (r.side === 'long' ? 'Long' : 'Short'),
    },
  ];

  return (
    <>
      {forwardPoints.length > 0 ? (
        <>
          <ChartFrame
            title="Forward outrights by tenor"
            subtitle="Ingested forward points against the period-end spot fix"
            height={300}
          >
            <ForwardCurve data={forwardPoints} />
          </ChartFrame>
          <SectionCard title="Forward points" subtitle="Outright and points by tenor" noPadding>
            <DataTable
              columns={[
                { key: 'tenor', header: 'Tenor', render: (r: ForwardPoint) => r.tenorLabel },
                {
                  key: 'outright',
                  header: 'Outright (GHS)',
                  numeric: true,
                  render: (r: ForwardPoint) => r.outright.toFixed(4),
                },
              ]}
              rows={forwardPoints}
              density="compact"
            />
          </SectionCard>
        </>
      ) : (
        <EmptyState
          Icon={LineChartIcon}
          title="No forward curve ingested"
          description="The market-data engine holds spot fixes only for this bank. Connect a market-data source or upload fx_rates rows with forward tenors to light up the forwards monitor — the tenor curve and outright table render automatically once points exist."
          action={
            <Link
              href="/data-engine/market-data"
              className="inline-flex items-center px-3 py-2 text-caption font-medium btn-primary"
            >
              Open market-data connections
            </Link>
          }
        />
      )}

      <SectionCard
        title="Spot reference"
        subtitle="Period-end GHS fixes used by the NOP engine — from the FX dashboard payload"
        noPadding
        footer={<span>Forward outrights will be monitored against these fixes.</span>}
      >
        <DataTable columns={spotColumns} rows={data.positions} density="compact" />
      </SectionCard>
    </>
  );
}
