'use client';

/**
 * API Push integration tab: connection details, the three-call flow with
 * copyable curl examples, readable per-entity record schemas, and the push
 * batch history. Content is authored in api-reference.ts against
 * docs/API_INTEGRATION.md.
 */

import PageHeader from '@/components/ui/PageHeader';
import {
  ConnectionCard,
  EntitySchemas,
  PushFlowSteps,
} from '@/components/data-engine/ApiPushGuide';
import BatchesTable from '@/components/data-engine/BatchesTable';

export default function ApiPushPage() {
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
      <div className="px-8 py-6 space-y-8 max-w-6xl">
        <ConnectionCard />
        <BatchesTable
          sourceSystem="API_PUSH"
          title="Push batch history"
          emptyDescription="No push batches yet. Run the three-call flow below — or the runnable example client — to commit the first one."
        />
        <PushFlowSteps />
        <EntitySchemas />
      </div>
    </>
  );
}
