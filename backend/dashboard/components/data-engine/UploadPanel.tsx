'use client';

/**
 * Upload & ingest: stage source files in the bank's encrypted temp tier,
 * then run each through the full pipeline — extract, translate, validate,
 * persist — sequentially, surfacing every batch outcome honestly. Selecting
 * the whole 17-file Sample Bank folder at once is the intended flow.
 */

import { useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Loader2, UploadCloud } from 'lucide-react';
import type { IngestionBatchRead } from '@aequoros/risk-service-api';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import { useUploadAndIngest } from '@/lib/api/ingestion';
import {
  BatchStatusPill,
  CountStrip,
  batchBlockerDetails,
  referenceRowTotal,
} from './shared';

const DEMO_AS_OF = '2026-04-30'; // business date of the Sample Bank dataset

type FileOutcome = {
  filename: string;
  state: 'queued' | 'running' | 'done' | 'failed';
  batch?: IngestionBatchRead;
  reused?: boolean;
  error?: string;
  seconds?: number;
};

export default function UploadPanel() {
  const { bank } = useBankContext();
  const inputRef = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [asOfDate, setAsOfDate] = useState(DEMO_AS_OF);
  const [outcomes, setOutcomes] = useState<FileOutcome[]>([]);
  const [running, setRunning] = useState(false);
  const uploadAndIngest = useUploadAndIngest(bank?.id);

  const runQueue = async () => {
    if (!files.length || running) return;
    setRunning(true);
    setOutcomes(
      files.map((file) => ({ filename: file.name, state: 'queued' as const })),
    );
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index];
      setOutcomes((previous) =>
        previous.map((outcome, i) =>
          i === index ? { ...outcome, state: 'running' } : outcome,
        ),
      );
      const startedAt = Date.now();
      try {
        // Sequential on purpose: later files reference earlier batches'
        // canonical state (counterparties, products, GL accounts).
        const { started } = await uploadAndIngest.mutateAsync({ file, asOfDate });
        setOutcomes((previous) =>
          previous.map((outcome, i) =>
            i === index
              ? {
                  ...outcome,
                  state: 'done',
                  batch: started.batch,
                  reused: started.reused,
                  seconds: (Date.now() - startedAt) / 1000,
                }
              : outcome,
          ),
        );
      } catch (error) {
        setOutcomes((previous) =>
          previous.map((outcome, i) =>
            i === index
              ? {
                  ...outcome,
                  state: 'failed',
                  error: isApiError(error) ? error.message : 'Upload failed.',
                  seconds: (Date.now() - startedAt) / 1000,
                }
              : outcome,
          ),
        );
      }
    }
    setRunning(false);
  };

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="text-h2 text-navy">Upload &amp; ingest</h2>
        <p className="mt-1 text-body text-slate">
          Files are stored encrypted with full provenance metadata. The raw
          source is retained immutably in the <code className="font-mono">raw</code>{' '}
          tier; every record traces back to its source cell. Select multiple
          files to queue them through the pipeline in order.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-0">
          <label className="block text-caption font-medium text-slate mb-1">
            Source files (.xlsx / .csv)
          </label>
          <input
            ref={inputRef}
            type="file"
            multiple
            accept=".xlsx,.csv,.tsv"
            onChange={(event) => setFiles(Array.from(event.target.files ?? []))}
            className="block text-body text-navy file:mr-3 file:px-3 file:py-1.5 file:rounded file:border file:border-border file:bg-surface file:text-caption file:font-medium file:text-navy hover:file:bg-border-light"
          />
        </div>
        <div>
          <label className="block text-caption font-medium text-slate mb-1">
            As-of date
          </label>
          <input
            type="date"
            value={asOfDate}
            onChange={(event) => setAsOfDate(event.target.value)}
            className="px-3 py-1.5 rounded border border-border text-body text-navy font-mono"
          />
        </div>
        <button
          type="button"
          disabled={!files.length || !asOfDate || !bank || running}
          onClick={() => void runQueue()}
          className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {running ? (
            <Loader2 size={15} className="animate-spin" aria-hidden />
          ) : (
            <UploadCloud size={15} aria-hidden />
          )}
          {running
            ? 'Ingesting…'
            : `Upload & ingest${files.length > 1 ? ` (${files.length} files)` : ''}`}
        </button>
      </div>

      {outcomes.length > 0 && (
        <div className="rounded border border-border divide-y divide-border-light">
          {outcomes.map((outcome, index) => (
            <div key={`${outcome.filename}-${index}`} className="p-4 space-y-3">
              <div className="flex flex-wrap items-center gap-3">
                {outcome.state === 'running' && (
                  <Loader2 size={14} className="animate-spin text-slate" aria-hidden />
                )}
                {outcome.batch ? (
                  <BatchStatusPill status={outcome.batch.status} />
                ) : outcome.state === 'failed' ? (
                  <BatchStatusPill status="failed" />
                ) : (
                  <span className="text-caption text-slate">
                    {outcome.state === 'queued' ? 'queued' : 'running…'}
                  </span>
                )}
                <span className="text-body text-navy font-medium">
                  {outcome.filename}
                </span>
                {outcome.seconds !== undefined && (
                  <span className="text-caption font-mono text-slate">
                    {outcome.seconds.toFixed(1)}s
                  </span>
                )}
                {outcome.reused && (
                  <span className="text-caption text-slate">
                    Identical content already ingested — existing batch returned
                    (idempotent).
                  </span>
                )}
                {outcome.batch && referenceRowTotal(outcome.batch) > 0 && (
                  <span className="text-caption text-slate">
                    {referenceRowTotal(outcome.batch)} reference rows
                  </span>
                )}
                {outcome.batch && (
                  <Link
                    href={`/data-engine/batches/${outcome.batch.id}`}
                    className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
                  >
                    Batch detail <ArrowRight size={13} aria-hidden />
                  </Link>
                )}
              </div>
              {outcome.batch && <CountStrip batch={outcome.batch} />}
              {outcome.batch &&
                batchBlockerDetails(outcome.batch).map((detail, i) => (
                  <div
                    key={i}
                    className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3"
                  >
                    <p className="text-body text-critical">{detail}</p>
                  </div>
                ))}
              {outcome.batch?.errorMessage && (
                <p className="text-body text-critical">{outcome.batch.errorMessage}</p>
              )}
              {outcome.error && (
                <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
                  <p className="text-body text-critical">{outcome.error}</p>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
