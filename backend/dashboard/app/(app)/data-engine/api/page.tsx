'use client';

/**
 * API Push tab. Console (connection + push batch history) is the landing view;
 * the formatted API reference (flow, runnable example, record schemas) sits
 * behind a secondary nav — the same content, presented like product docs.
 */

import { useState } from 'react';
import PageHeader from '@/components/ui/PageHeader';
import SubTabs from '@/components/ui/SubTabs';
import {
  ConnectionCard,
  EntitySchemas,
  ExampleClient,
  PushFlowSteps,
} from '@/components/data-engine/ApiPushGuide';
import BatchesTable from '@/components/data-engine/BatchesTable';

const VIEWS = [
  { key: 'console', label: 'Console' },
  { key: 'reference', label: 'API reference' },
];

export default function ApiPushPage() {
  const [view, setView] = useState('console');
  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'API Push' },
        ]}
        title="API Push"
        subtitle="Middleware POSTs JSON through the push endpoints — same pipeline, validation gating, and lineage as file uploads."
      />
      <div className="px-8 py-6 max-w-6xl space-y-6">
        <SubTabs items={VIEWS} active={view} onChange={setView} />
        {view === 'console' ? (
          <div className="space-y-8">
            <ConnectionCard />
            <BatchesTable
              sourceSystem="API_PUSH"
              title="Push batch history"
              emptyDescription="No push batches yet. Run the flow under API reference — or the runnable example — to commit the first one."
            />
          </div>
        ) : (
          <div className="space-y-8">
            <PushFlowSteps />
            <ExampleClient />
            <EntitySchemas />
          </div>
        )}
      </div>
    </>
  );
}
