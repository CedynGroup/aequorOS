'use client';

/**
 * Sync panel for one connection: run an on-demand extraction for an as-of date,
 * show the returned batch id + terminal status and record gating counts, and —
 * once the batch is loaded — surface its ETL preprocess and validation reports.
 * The demo bank's deliberate GL drift lands as ACCEPTED_WITH_WARNINGS.
 */

import { useState } from 'react';
import Link from 'next/link';
import { ArrowRight, DownloadCloud, Info, Loader2 } from 'lucide-react';
import type { DatabaseConnectionSyncResult } from '@aequoros/risk-service-api';
import { isApiError } from '@/lib/api/client';
import { useIngestionBatch } from '@/lib/api/ingestion';
import { useSyncDatabaseConnection } from '@/lib/api/database-direct';
import BatchReport from './BatchReport';
import { SyncStatusPill } from './shared';
import { fmtLocale } from '@/lib/format';

function errorMessage(error: unknown): string {
  if (isApiError(error)) return error.message;
  if (error instanceof Error) return error.message;
  return 'The sync failed.';
}

export default function SyncPanel({
  bankId,
  connectionId,
  disabled,
}: {
  bankId: string;
  connectionId: string;
  disabled?: boolean;
}) {
  const sync = useSyncDatabaseConnection(bankId);
  const [asOfDate, setAsOfDate] = useState('');
  const [result, setResult] = useState<DatabaseConnectionSyncResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Once a sync returns a batch id, load the immutable batch to surface its
  // ETL preprocess + validation reports.
  const batchQuery = useIngestionBatch(bankId, result?.batchId);

  const runSync = async () => {
    setError(null);
    setResult(null);
    try {
      const outcome = await sync.mutateAsync({
        connectionId,
        asOfDate: asOfDate || undefined,
        reason: 'On-demand sync from the Data Engine console.',
      });
      setResult(outcome);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  };

  return (
    <div className="rounded border border-border p-4 space-y-4 bg-surface-alt">
      <div className="flex flex-wrap items-end gap-3">
        <div>
          <label
            htmlFor={`sync-date-${connectionId}`}
            className="block text-caption font-medium text-slate mb-1"
          >
            As-of date
          </label>
          <input
            id={`sync-date-${connectionId}`}
            type="date"
            value={asOfDate}
            onChange={(event) => setAsOfDate(event.target.value)}
            className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
          />
        </div>
        <button
          type="button"
          onClick={() => void runSync()}
          disabled={sync.isPending || disabled}
          className="inline-flex items-center gap-1.5 px-3 py-2 rounded text-caption font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {sync.isPending ? (
            <Loader2 size={13} className="animate-spin" aria-hidden />
          ) : (
            <DownloadCloud size={13} aria-hidden />
          )}
          Sync {asOfDate || 'today'}
        </button>
      </div>
      <p className="text-caption text-slate">
        Extracts through the adapter, runs the ETL preprocess + dedup pass, validates,
        and persists an immutable batch. Re-syncing a date supersedes rather than
        duplicates.
      </p>

      {error && (
        <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
          <p className="text-body text-critical">{error}</p>
        </div>
      )}

      {result && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <SyncStatusPill status={result.status} />
            <span className="text-caption font-mono text-slate">
              batch {result.batchId.slice(0, 13)}
            </span>
            {result.reused && (
              <span className="text-caption text-slate">
                reused an existing batch for this date
              </span>
            )}
            <Link
              href={`/data-engine/batches/${result.batchId}`}
              className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
            >
              Full batch detail <ArrowRight size={12} aria-hidden />
            </Link>
          </div>

          {result.asOfNote && (
            <p className="flex items-start gap-2 rounded border border-warning/30 bg-warning-light/40 px-3 py-2 text-caption text-navy">
              <Info size={13} className="mt-0.5 shrink-0 text-warning" aria-hidden />
              <span>{result.asOfNote}</span>
            </p>
          )}

          <dl className="grid grid-cols-2 sm:grid-cols-3 gap-x-6 gap-y-2 text-body">
            <div>
              <dt className="text-caption text-slate">Records extracted</dt>
              <dd className="font-mono text-navy tabular-nums">
                {result.recordsExtracted.toLocaleString(fmtLocale())}
              </dd>
            </div>
            <div>
              <dt className="text-caption text-slate">Records accepted</dt>
              <dd className="font-mono text-navy tabular-nums">
                {result.recordsAccepted.toLocaleString(fmtLocale())}
              </dd>
            </div>
          </dl>

          {batchQuery.isPending ? (
            <p className="inline-flex items-center gap-2 text-caption text-slate">
              <Loader2 size={13} className="animate-spin" aria-hidden />
              Loading the batch reports…
            </p>
          ) : batchQuery.isError ? (
            <p className="text-caption text-slate">
              The batch was created; open the full batch detail for its reports.
            </p>
          ) : batchQuery.data ? (
            <BatchReport batch={batchQuery.data} />
          ) : null}
        </div>
      )}
    </div>
  );
}
