'use client';

/**
 * Ingestion batch history: every attempt is immutable history — accepted,
 * rejected, and failed batches all stay visible with their diagnostics.
 * Reused across the console: unfiltered (recent, overview) or filtered to
 * one source system (integration tabs).
 */

import Link from 'next/link';
import type { IngestionBatchRead } from '@aequoros/risk-service-api';
import { useBankContext } from '@/components/shell/BankContext';
import DataTable, { type Column } from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useIngestionBatches, type IngestionSourceSystem } from '@/lib/api/ingestion';
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
    key: 'system',
    header: 'Via',
    render: (batch) => (
      <span className="font-mono text-caption text-slate">{batch.sourceSystem}</span>
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
    header: (
      <span
        title="Data-quality flags are persisted rows that participate in calculations; rejected rows are excluded."
        className="cursor-help underline decoration-dotted decoration-border underline-offset-2"
      >
        Flagged / Rejected
      </span>
    ),
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

export default function BatchesTable({
  sourceSystem,
  limit,
  title = 'Ingestion history',
  emptyDescription = 'Activate a mapping and upload a source file to run the first ingestion.',
}: {
  /** Restrict history to one source system (server-side filter). */
  sourceSystem?: IngestionSourceSystem;
  /** Show only the most recent N batches. */
  limit?: number;
  title?: string;
  emptyDescription?: string;
}) {
  const { bank } = useBankContext();
  const batchesQuery = useIngestionBatches(bank?.id, sourceSystem);

  if (batchesQuery.isError) {
    return (
      <ErrorPanel
        error={batchesQuery.error}
        onRetry={() => void batchesQuery.refetch()}
        title="Could not load ingestion history"
      />
    );
  }

  const all = batchesQuery.data?.batches ?? [];
  const batches = limit !== undefined ? all.slice(0, limit) : all;

  return (
    <section className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-h2 text-navy">{title}</h2>
        {limit !== undefined && all.length > batches.length && (
          <p className="text-caption text-slate">
            Showing the {batches.length} most recent of {all.length} batches
          </p>
        )}
      </div>
      {batches.length === 0 ? (
        <EmptyState title="No ingestion batches yet" description={emptyDescription} />
      ) : (
        <div className="card overflow-hidden">
          <DataTable columns={columns} rows={batches} density="compact" />
        </div>
      )}
    </section>
  );
}
