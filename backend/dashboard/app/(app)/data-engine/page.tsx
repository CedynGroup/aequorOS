'use client';

/**
 * Data Engine overview: cross-source console. Integration status cards with
 * live per-source stats, the canonical-model KPI strip, recent batches across
 * all sources, and the cross-source Activate panel. Everything specific to
 * one integration lives in its tab.
 */

import PageHeader from '@/components/ui/PageHeader';
import {
  CanonicalSummaryStrip,
  IntegrationCards,
} from '@/components/data-engine/OverviewPanels';
import BatchesTable from '@/components/data-engine/BatchesTable';
import ActivatePanel from '@/components/data-engine/ActivatePanel';
import ConnectionHealthPanel from '@/components/data-engine/ConnectionHealthPanel';
import LiveStatusCard from '@/components/live/LiveStatusCard';

export default function DataEngineOverviewPage() {
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Data Engine' }, { label: 'Overview' }]}
        title="Data Engine"
        subtitle="Connect data sources, ingest into the canonical model, and trace every record back to its source."
      />
      <div className="px-8 py-6 space-y-8 max-w-6xl">
        <IntegrationCards />
        <ConnectionHealthPanel />
        <LiveStatusCard />
        <CanonicalSummaryStrip />
        <BatchesTable
          limit={8}
          title="Recent ingestion — all sources"
          emptyDescription="No batches yet. Open the Excel & CSV tab to upload files, or push data through the API."
        />
        <ActivatePanel />
      </div>
    </>
  );
}
