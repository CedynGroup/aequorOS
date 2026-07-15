'use client';

/**
 * Ingestion batch history: every attempt is immutable history — accepted,
 * rejected, and failed batches all stay visible with their diagnostics.
 */

import Link from 'next/link';
import type { IngestionBatchRead } from '@aequoros/risk-service-api';
import { useBankContext } from '@/components/shell/BankContext';
import DataTable, { type Column } from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useIngestionBatches } from '@/lib/api/ingestion';
import { BatchStatusPill, formatDate, formatDateTime } from './shared';

const columns: Column<IngestionBatchRead>[] = [
  {
    key: 'started',
    header: 'Started',
    render: (batch) => (
      <span className="font-mono text-caption text-slate">
        {formatDateTime(batch.startedAt ?? batch.createdAt)}
      </span>
    ),
  },
  {
    key: 'as_of',
    header: 'As of',
    render: (batch) => (
      <span className="font-mono text-caption">{formatDate(batch.asOfDate)}</span>
    ),
  },
  {
    key: 'source',
    header: 'Source',
    render: (batch) => (
      <span className="text-body text-navy">
        {batch.rawArtifactPath?.split('/').pop() ?? batch.sourceSystem}
      </span>
    ),
  },
  {
    key: 'status',
    header: 'Status',
    render: (batch) => <BatchStatusPill status={batch.status} />,
  },
  {
    key: 'accepted',
    header: 'Accepted',
    numeric: true,
    render: (batch) => <span className="font-mono">{batch.recordsAccepted}</span>,
  },
  {
    key: 'issues',
    header: 'Warn / Err',
    numeric: true,
    render: (batch) => (
      <span className="font-mono">
        <span className={batch.recordsWarning ? 'text-warning' : ''}>
          {batch.recordsWarning}
        </span>
        {' / '}
        <span className={batch.recordsError ? 'text-critical' : ''}>
          {batch.recordsError}
        </span>
      </span>
    ),
  },
  {
    key: 'detail',
    header: '',
    align: 'right',
    render: (batch) => (
      <Link
        href={`/data-engine/batches/${batch.id}`}
        className="text-caption font-medium text-action hover:text-action-hover"
      >
        Detail
      </Link>
    ),
  },
];

export default function BatchesTable() {
  const { bank } = useBankContext();
  const batchesQuery = useIngestionBatches(bank?.id);

  if (batchesQuery.isError) {
    return (
      <ErrorPanel
        error={batchesQuery.error}
        onRetry={() => void batchesQuery.refetch()}
        title="Could not load ingestion history"
      />
    );
  }

  const batches = batchesQuery.data?.batches ?? [];

  return (
    <section className="space-y-3">
      <h2 className="text-h2 text-navy">Ingestion history</h2>
      {batches.length === 0 ? (
        <EmptyState
          title="No ingestion batches yet"
          description="Activate a mapping and upload a source file to run the first ingestion."
        />
      ) : (
        <div className="card overflow-hidden">
          <DataTable columns={columns} rows={batches} density="compact" />
        </div>
      )}
    </section>
  );
}
