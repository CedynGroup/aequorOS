'use client';

/**
 * Excel & CSV integration tab: everything file-specific — the active mapping
 * configuration + starter templates, the upload & ingest panel, and the
 * ingestion history filtered to file batches.
 */

import PageHeader from '@/components/ui/PageHeader';
import MappingPanel from '@/components/data-engine/MappingPanel';
import UploadPanel from '@/components/data-engine/UploadPanel';
import BatchesTable from '@/components/data-engine/BatchesTable';

export default function ExcelCsvPage() {
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Excel & CSV' },
        ]}
        title="Excel & CSV"
        subtitle="Workbook and CSV drops with mapping-driven translation, cell-level lineage, and validation gating."
      />
      <div className="px-8 py-6 space-y-8 max-w-6xl">
        <MappingPanel />
        <UploadPanel />
        <BatchesTable
          sourceSystem="EXCEL_CSV"
          title="File ingestion history"
          emptyDescription="Activate a mapping above and upload a source file to run the first ingestion."
        />
      </div>
    </>
  );
}
