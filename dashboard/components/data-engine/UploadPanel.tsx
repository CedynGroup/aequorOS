'use client';

/**
 * Upload & ingest: stage a source file in the bank's encrypted temp tier,
 * then run it through the full pipeline — extract, translate, validate,
 * persist — and surface the batch outcome inline.
 */

import { useRef, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Loader2, UploadCloud } from 'lucide-react';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import { useUploadAndIngest } from '@/lib/api/ingestion';
import { BatchStatusPill, CountStrip } from './shared';

const DEMO_AS_OF = '2026-04-30'; // business date of the Sample Bank dataset

export default function UploadPanel() {
  const { bank } = useBankContext();
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [asOfDate, setAsOfDate] = useState(DEMO_AS_OF);
  const uploadAndIngest = useUploadAndIngest(bank?.id);

  const result = uploadAndIngest.data?.started;

  return (
    <section className="card p-5 space-y-4">
      <div>
        <h2 className="text-h2 text-navy">Upload &amp; ingest</h2>
        <p className="mt-1 text-body text-slate">
          Files are stored encrypted with full provenance metadata. The raw
          source is retained immutably in the <code className="font-mono">raw</code>{' '}
          tier; every record traces back to its source cell.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-4">
        <div className="min-w-0">
          <label className="block text-caption font-medium text-slate mb-1">
            Source file (.xlsx / .csv)
          </label>
          <input
            ref={inputRef}
            type="file"
            accept=".xlsx,.csv,.tsv"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
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
          disabled={!file || !asOfDate || !bank || uploadAndIngest.isPending}
          onClick={() => {
            if (file) uploadAndIngest.mutate({ file, asOfDate });
          }}
          className="inline-flex items-center gap-2 px-4 py-2 rounded text-body font-medium bg-action text-white hover:bg-action-hover disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {uploadAndIngest.isPending ? (
            <Loader2 size={15} className="animate-spin" aria-hidden />
          ) : (
            <UploadCloud size={15} aria-hidden />
          )}
          {uploadAndIngest.isPending ? 'Ingesting…' : 'Upload & ingest'}
        </button>
      </div>

      {uploadAndIngest.isError && (
        <div className="rounded border border-critical/30 bg-critical-light/40 px-4 py-3">
          <p className="text-body text-critical">
            {isApiError(uploadAndIngest.error)
              ? uploadAndIngest.error.message
              : 'Upload failed.'}
          </p>
        </div>
      )}

      {result && (
        <div className="rounded border border-border p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-3">
            <BatchStatusPill status={result.batch.status} />
            {uploadAndIngest.data?.staged && (
              <span className="text-body text-navy font-medium">
                {uploadAndIngest.data.staged.filename}
              </span>
            )}
            {result.reused && (
              <span className="text-caption text-slate">
                Identical content already ingested — existing batch returned
                (idempotent).
              </span>
            )}
            <Link
              href={`/data-engine/batches/${result.batch.id}`}
              className="ml-auto inline-flex items-center gap-1 text-caption font-medium text-action hover:text-action-hover"
            >
              Batch detail <ArrowRight size={13} aria-hidden />
            </Link>
          </div>
          <CountStrip batch={result.batch} />
          {result.batch.errorMessage && (
            <p className="text-body text-critical">{result.batch.errorMessage}</p>
          )}
        </div>
      )}
    </section>
  );
}
