'use client';

/**
 * Canonical positions: the source-agnostic balance sheet the calculation
 * modules read. Expanding a row walks its lineage graph back to the exact
 * adapter extraction that produced it.
 */

import { useState } from 'react';
import { ChevronDown, ChevronRight, GitCommitHorizontal } from 'lucide-react';
import type { CanonicalPositionRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import EmptyState from '@/components/ui/EmptyState';
import { ErrorPanel, PageSkeleton } from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import { useCanonicalPositions, useLineageWalk } from '@/lib/api/ingestion';
import { formatDate, formatDateTime } from '@/components/data-engine/shared';

function formatAmount(value: string | null): string {
  if (value === null || value === undefined) return '—';
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return value;
  return numeric.toLocaleString('en-GH', { maximumFractionDigits: 2 });
}

function LineageChain({ lineageId }: { lineageId: string }) {
  const walk = useLineageWalk(lineageId);
  if (walk.isPending) {
    return <p className="text-caption text-slate px-4 py-3">Walking lineage…</p>;
  }
  if (walk.isError) {
    return (
      <p className="text-caption text-critical px-4 py-3">
        Could not walk lineage for this record.
      </p>
    );
  }
  const nodes = [...walk.data.nodes].reverse(); // oldest (extract) first
  return (
    <ol className="px-4 py-3 space-y-2">
      {nodes.map((node, index) => (
        <li key={node.id} className="flex items-start gap-3">
          <span className="mt-0.5 shrink-0 inline-flex items-center justify-center w-5 h-5 rounded-full bg-nav text-white text-[10px] font-mono">
            {index + 1}
          </span>
          <div className="min-w-0">
            <p className="text-body text-navy font-medium">
              {node.operationType.replaceAll('_', ' ')}
            </p>
            <p className="text-caption font-mono text-slate truncate">
              {node.operationRef} · {formatDateTime(node.occurredAt)}
            </p>
          </div>
        </li>
      ))}
    </ol>
  );
}

export default function CanonicalPositionsPage() {
  const { bank } = useBankContext();
  const [offset, setOffset] = useState(0);
  const positionsQuery = useCanonicalPositions(bank?.id, offset);
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Canonical Data' },
        ]}
        title="Canonical positions"
        subtitle="The source-agnostic balance sheet produced by ingestion. Expand any row to trace it back to its source extraction."
      />
      <div className="px-8 py-6 max-w-6xl">
        {positionsQuery.isPending && <PageSkeleton />}
        {positionsQuery.isError && (
          <ErrorPanel
            error={positionsQuery.error}
            onRetry={() => void positionsQuery.refetch()}
          />
        )}
        {positionsQuery.data &&
          (positionsQuery.data.positions.length === 0 ? (
            <EmptyState
              title="No canonical positions yet"
              description="Ingest a positions file from the Excel & CSV tab (or push via the API) to populate the canonical model."
            />
          ) : (
            <>
              <div className="card overflow-hidden">
                <table className="w-full text-body border-collapse">
                  <thead>
                    <tr className="border-b border-border bg-surface">
                      {['', 'Reference', 'Type', 'Ccy', 'Balance', 'Rate', 'Maturity', 'As of', 'Status'].map(
                        (header, index) => (
                          <th
                            key={index}
                            className={`py-2 px-4 text-micro font-medium uppercase tracking-wider text-slate ${
                              header === 'Balance' || header === 'Rate' ? 'text-right' : 'text-left'
                            }`}
                          >
                            {header}
                          </th>
                        ),
                      )}
                    </tr>
                  </thead>
                  <tbody>
                    {positionsQuery.data.positions.map((position: CanonicalPositionRead) => (
                      <PositionRow
                        key={position.id}
                        position={position}
                        expanded={expanded === position.id}
                        onToggle={() =>
                          setExpanded(expanded === position.id ? null : position.id)
                        }
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="mt-4 flex items-center justify-between gap-3">
                <p className="text-caption text-slate">
                  Showing {(positionsQuery.data.offset + 1).toLocaleString('en-US')}–
                  {(
                    positionsQuery.data.offset + positionsQuery.data.positions.length
                  ).toLocaleString('en-US')}{' '}
                  of {positionsQuery.data.total.toLocaleString('en-US')} canonical
                  positions
                </p>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      setOffset(Math.max(0, offset - positionsQuery.data.limit))
                    }
                    disabled={positionsQuery.data.offset === 0}
                    className="px-2.5 py-1.5 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-40 disabled:pointer-events-none"
                  >
                    Prev
                  </button>
                  <button
                    type="button"
                    onClick={() => setOffset(offset + positionsQuery.data.limit)}
                    disabled={
                      positionsQuery.data.offset + positionsQuery.data.limit >=
                      positionsQuery.data.total
                    }
                    className="px-2.5 py-1.5 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-40 disabled:pointer-events-none"
                  >
                    Next
                  </button>
                </div>
              </div>
            </>
          ))}
      </div>
    </>
  );
}

function PositionRow({
  position,
  expanded,
  onToggle,
}: {
  position: CanonicalPositionRead;
  expanded: boolean;
  onToggle: () => void;
}) {
  return (
    <>
      <tr
        className="border-b border-border-light hover:bg-surface-alt cursor-pointer"
        onClick={onToggle}
      >
        <td className="py-2 pl-4 w-8 text-slate">
          {expanded ? (
            <ChevronDown size={14} aria-hidden />
          ) : (
            <ChevronRight size={14} aria-hidden />
          )}
        </td>
        <td className="py-2 px-4 font-mono text-caption text-navy">
          {position.sourceReference}
        </td>
        <td className="py-2 px-4">{position.positionType}</td>
        <td className="py-2 px-4 font-mono text-caption">{position.currency}</td>
        <td className="py-2 px-4 text-right font-mono">
          {formatAmount(position.balance)}
        </td>
        <td className="py-2 px-4 text-right font-mono text-caption">
          {position.interestRate !== null && position.interestRate !== undefined
            ? `${(Number(position.interestRate) * 100).toFixed(2)}%`
            : '—'}
        </td>
        <td className="py-2 px-4 font-mono text-caption">
          {formatDate(position.contractualMaturity)}
        </td>
        <td className="py-2 px-4 font-mono text-caption">
          {formatDate(position.asOfDate)}
        </td>
        <td className="py-2 px-4 text-caption">{position.validationStatus}</td>
      </tr>
      {expanded && (
        <tr className="border-b border-border-light bg-surface-alt">
          <td colSpan={9}>
            <div className="flex items-center gap-2 px-4 pt-3 text-caption font-medium text-slate uppercase tracking-wider">
              <GitCommitHorizontal size={13} aria-hidden /> Lineage
            </div>
            <LineageChain lineageId={position.lineageId} />
          </td>
        </tr>
      )}
    </>
  );
}
