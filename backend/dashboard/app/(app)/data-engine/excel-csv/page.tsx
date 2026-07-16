'use client';

/**
 * Excel & CSV tab. Upload (mapping + file ingest + history) is the landing
 * view; downloadable templates sit behind a secondary nav so customers can
 * grab a correctly-shaped file, fill it, and upload.
 */

import { useState } from 'react';
import PageHeader from '@/components/ui/PageHeader';
import SubTabs from '@/components/ui/SubTabs';
import MappingPanel from '@/components/data-engine/MappingPanel';
import UploadPanel from '@/components/data-engine/UploadPanel';
import TemplatesPanel from '@/components/data-engine/TemplatesPanel';
import BatchesTable from '@/components/data-engine/BatchesTable';

const VIEWS = [
  { key: 'upload', label: 'Upload' },
  { key: 'templates', label: 'Templates' },
];

export default function ExcelCsvPage() {
  const [view, setView] = useState('upload');
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
      <div className="px-8 py-6 max-w-6xl space-y-6">
        <SubTabs items={VIEWS} active={view} onChange={setView} />
        {view === 'upload' ? (
          <div className="space-y-8">
            <MappingPanel />
            <UploadPanel />
            <BatchesTable
              sourceSystem="EXCEL_CSV"
              title="File ingestion history"
              emptyDescription="Activate a mapping above and upload a source file to run the first ingestion."
            />
          </div>
        ) : (
          <TemplatesPanel />
        )}
      </div>
    </>
  );
}
