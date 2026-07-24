'use client';

/**
 * Client-side detail drawer for one canonical position: every field the
 * payload carries, the lineage chain back to the source extraction, and a
 * data-quality chip linking to the ingestion batch the lineage resolves to.
 */

import Link from 'next/link';
import { ArrowUpRight, GitCommitHorizontal, X } from 'lucide-react';
import type { CanonicalPositionRead } from '@aequoros/risk-service-api';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import { useLineageWalk } from '@/lib/api/ingestion';
import { fmtDateUTC, fmtTimestamp, labelize, num } from '@/lib/api/values';
import { fmtLocale } from '@/lib/format';

export function validationTone(status: string): StatusTone {
  switch (status) {
    case 'accepted':
      return 'compliant';
    case 'warning':
      return 'amber';
    case 'error':
    case 'blocked':
      return 'critical';
    default:
      return 'pending';
  }
}

export function fmtBalance(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return value;
  return parsed.toLocaleString(fmtLocale(), { maximumFractionDigits: 2 });
}

export function fmtRate(value: string | null | undefined): string {
  if (value === null || value === undefined) return '—';
  return `${(num(value) * 100).toFixed(2)}%`;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <p className="text-micro font-medium text-slate uppercase tracking-wider">{label}</p>
      <p className="mt-0.5 text-body text-navy font-mono tnum break-all">{children}</p>
    </div>
  );
}

export default function PositionDrawer({
  position,
  onClose,
}: {
  position: CanonicalPositionRead;
  onClose: () => void;
}) {
  const walk = useLineageWalk(position.lineageId);
  // Nodes come newest-first; the batch is attached to the extraction root.
  const batchId =
    walk.data?.nodes
      .slice()
      .reverse()
      .find((node) => node.ingestionBatchId !== null)?.ingestionBatchId ?? null;

  return (
    <>
      <div
        className="fixed inset-0 bg-navy/30 z-40"
        onClick={onClose}
        aria-hidden
      />
      <aside
        className="fixed inset-y-0 right-0 z-50 w-full max-w-md bg-surface-raised border-l border-border shadow-xl overflow-y-auto"
        role="dialog"
        aria-label={`Position ${position.sourceReference}`}
      >
        <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-border-light sticky top-0 bg-surface-raised">
          <div className="min-w-0">
            <p className="text-micro font-medium text-slate uppercase tracking-wider">
              {labelize(position.positionType)}
            </p>
            <h2 className="text-h3 text-navy font-mono truncate">
              {position.sourceReference}
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close details"
            className="shrink-0 p-1.5 rounded-md text-slate hover:text-navy hover:bg-surface"
          >
            <X size={16} aria-hidden />
          </button>
        </div>

        <div className="px-5 py-4 space-y-5">
          {/* Data quality */}
          <div className="card px-4 py-3 flex items-center justify-between gap-3">
            <span className="inline-flex items-center gap-2">
              <span className="text-caption font-medium text-slate">Data quality</span>
              <StatusPill tone={validationTone(position.validationStatus)}>
                {position.validationStatus}
              </StatusPill>
            </span>
            {batchId ? (
              <Link
                href={`/data-engine/batches/${batchId}`}
                className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline whitespace-nowrap"
              >
                View source batch
                <ArrowUpRight size={12} aria-hidden />
              </Link>
            ) : (
              <span className="text-caption text-slate-light">
                {walk.isPending ? 'Resolving batch…' : 'No batch in lineage'}
              </span>
            )}
          </div>

          {/* Fields */}
          <div className="grid grid-cols-2 gap-x-4 gap-y-4">
            <Field label="Balance">
              {fmtBalance(position.balance)} {position.currency}
            </Field>
            <Field label="Interest rate">
              {fmtRate(position.interestRate)}
              {position.rateType ? ` · ${position.rateType}` : ''}
            </Field>
            <Field label="Contractual maturity">
              {position.contractualMaturity
                ? fmtDateUTC(position.contractualMaturity)
                : '—'}
            </Field>
            <Field label="As of">{fmtDateUTC(position.asOfDate)}</Field>
            <Field label="Source system">{position.sourceSystem}</Field>
            <Field label="Snapshot">
              {position.snapshotId ? position.snapshotId.slice(0, 8) : 'none'}
            </Field>
          </div>

          {/* Lineage */}
          <div>
            <p className="flex items-center gap-2 text-caption font-medium text-slate uppercase tracking-wider mb-2">
              <GitCommitHorizontal size={13} aria-hidden /> Lineage
            </p>
            {walk.isPending && (
              <p className="text-caption text-slate">Walking lineage…</p>
            )}
            {walk.isError && (
              <p className="text-caption text-critical">
                Could not walk lineage for this record.
              </p>
            )}
            {walk.data && (
              <ol className="space-y-2.5">
                {[...walk.data.nodes].reverse().map((node, index) => (
                  <li key={node.id} className="flex items-start gap-3">
                    <span className="mt-0.5 shrink-0 inline-flex items-center justify-center w-5 h-5 rounded-full bg-surface border border-border text-slate text-[10px] font-mono">
                      {index + 1}
                    </span>
                    <div className="min-w-0">
                      <p className="text-body text-navy font-medium">
                        {labelize(node.operationType)}
                      </p>
                      <p className="text-caption font-mono text-slate truncate">
                        {node.operationRef} · {fmtTimestamp(node.occurredAt)}
                      </p>
                    </div>
                  </li>
                ))}
              </ol>
            )}
          </div>
        </div>
      </aside>
    </>
  );
}
