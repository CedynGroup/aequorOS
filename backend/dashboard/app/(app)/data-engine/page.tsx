'use client';

import PageHeader from '@/components/ui/PageHeader';
import SourcesPanel from '@/components/data-engine/SourcesPanel';
import UploadPanel from '@/components/data-engine/UploadPanel';
import BatchesTable from '@/components/data-engine/BatchesTable';
import ActivatePanel from '@/components/data-engine/ActivatePanel';

export default function DataEnginePage() {
  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Data Engine' }]}
        title="Data Engine"
        subtitle="Connect data sources, ingest into the canonical model, and trace every record back to its source."
      />
      <div className="px-8 py-6 space-y-8 max-w-6xl">
        <SourcesPanel />
        <UploadPanel />
        <BatchesTable />
        <ActivatePanel />
      </div>
    </>
  );
}
