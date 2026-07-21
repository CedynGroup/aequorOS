'use client';

/**
 * Market Data tab of the Data Engine console (spec §9.3 "Market Data
 * Sources"). Source cards for every connection (vendor or manual), the
 * add-source stepper (vendor pick → credentials → scopes → test → schedule →
 * activate), and the manual-upload block: template downloads plus the
 * file+as-of upload that lands canonical market data and auto-recomputes
 * dependent modules. Vendor concepts stay behind the adapters; this page
 * speaks scopes, freshness, and quota only.
 */

import { useState } from 'react';
import { Plus, UploadCloud, FileSpreadsheet, Loader2 } from 'lucide-react';
import type { MarketDataUploadRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import { useBankContext } from '@/components/shell/BankContext';
import { isApiError } from '@/lib/api/client';
import {
  useMarketDataConnections,
  useMarketDataQuota,
  useUploadMarketData,
} from '@/lib/api/hooks';
import SourceCard from '@/components/market-data/SourceCards';
import AddSourcePanel from '@/components/market-data/AddSourcePanel';
import { TEMPLATE_KINDS, downloadTemplate } from '@/components/market-data/shared';

export default function MarketDataPage() {
  const { bank } = useBankContext();
  const connections = useMarketDataConnections(bank?.id);
  const quota = useMarketDataQuota(bank?.id);
  const [adding, setAdding] = useState(false);

  const rows = connections.data?.connections ?? [];
  const quotaByVendor = new Map(
    (quota.data?.vendors ?? []).map((entry) => [entry.vendor, entry])
  );

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Data Engine', href: '/data-engine' },
          { label: 'Market Data' },
        ]}
        title="Market Data"
        subtitle="Yield curves, FX rates, ratings, and macro forecasts from Bloomberg, LSEG (formerly Refinitiv), or manual upload — normalized into one canonical model with freshness and attribution."
      />
      <div className="px-8 py-6 max-w-6xl space-y-8">
        <section className="space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-h3 text-navy">Connected sources</h2>
            {!adding && (
              <button
                type="button"
                onClick={() => setAdding(true)}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
              >
                <Plus size={13} aria-hidden />
                Connect a source
              </button>
            )}
          </div>
          {connections.isLoading ? (
            <div className="card p-6 text-body text-slate inline-flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" aria-hidden />
              Loading market data sources…
            </div>
          ) : rows.length === 0 && !adding ? (
            <div className="card p-8 text-center space-y-2">
              <p className="text-body text-navy font-medium">
                No market data sources connected yet
              </p>
              <p className="text-caption text-slate">
                Connect Bloomberg or LSEG, or use manual upload below —
                calculations consume the same canonical scopes either way.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
              {rows.map((connection) => (
                <SourceCard
                  key={connection.id}
                  bankId={bank?.id ?? ''}
                  connection={connection}
                  quota={quotaByVendor.get(connection.vendor)}
                />
              ))}
            </div>
          )}
          {adding && bank && (
            <AddSourcePanel
              bankId={bank.id}
              existingVendors={rows.map((row) => row.vendor)}
              onDone={() => setAdding(false)}
            />
          )}
        </section>

        <ManualUploadSection bankId={bank?.id} />
      </div>
    </>
  );
}

function ManualUploadSection({ bankId }: { bankId: string | undefined }) {
  const upload = useUploadMarketData(bankId);
  const [file, setFile] = useState<File | null>(null);
  const [asOfDate, setAsOfDate] = useState('');
  const result: MarketDataUploadRead | undefined = upload.data;

  return (
    <section className="space-y-4">
      <h2 className="text-h3 text-navy">Manual upload</h2>
      <div className="card p-6 space-y-5">
        <div>
          <p className="text-body text-navy font-medium">1. Download a template</p>
          <p className="text-caption text-slate mb-3">
            One template per scope category. Rates are entered as percentages
            (e.g. 15.80); AequorOS normalizes on ingest.
          </p>
          <div className="flex flex-wrap gap-2">
            {TEMPLATE_KINDS.map(({ kind, label }) => (
              <button
                key={kind}
                type="button"
                onClick={() => void downloadTemplate(kind)}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-caption text-navy border border-line rounded-md hover:bg-surface"
              >
                <FileSpreadsheet size={13} aria-hidden />
                {label}
              </button>
            ))}
          </div>
        </div>
        <div className="border-t border-line pt-5">
          <p className="text-body text-navy font-medium">2. Upload the filled file</p>
          <p className="text-caption text-slate mb-3">
            Multi-sheet workbooks are supported (one scope category per sheet).
            Accepted data lands in the canonical model and dependent modules
            recompute automatically.
          </p>
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (file && asOfDate) upload.mutate({ file, asOfDate });
            }}
          >
            <label className="block">
              <span className="block text-caption text-slate mb-1">File (.xlsx / .csv)</span>
              <input
                type="file"
                accept=".xlsx,.csv"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
                className="block text-caption text-navy file:mr-3 file:px-3 file:py-1.5 file:border file:border-line file:rounded-md file:bg-surface file:text-caption file:text-navy"
              />
            </label>
            <label className="block">
              <span className="block text-caption text-slate mb-1">As-of date</span>
              <input
                type="date"
                value={asOfDate}
                onChange={(event) => setAsOfDate(event.target.value)}
                className="px-3 py-1.5 text-caption text-navy border border-line rounded-md"
              />
            </label>
            <button
              type="submit"
              disabled={!file || !asOfDate || upload.isPending}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {upload.isPending ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <UploadCloud size={13} aria-hidden />
              )}
              Upload
            </button>
          </form>
          {upload.error && (
            <p className="mt-3 text-caption text-critical">
              {isApiError(upload.error)
                ? upload.error.message
                : 'Upload failed — check the file against its template.'}
            </p>
          )}
          {result && (
            <div className="mt-4 rounded-md border border-line bg-surface px-4 py-3 space-y-1">
              <p className="text-caption text-navy font-medium">
                Batch {result.status.replaceAll('_', ' ')} —{' '}
                {result.canonicalRecordsProduced} canonical records across{' '}
                {result.scopes.length} scope{result.scopes.length === 1 ? '' : 's'}
              </p>
              <p className="text-caption text-slate">{result.scopes.join(', ')}</p>
              {result.warnings.map((warning) => (
                <p key={warning} className="text-caption text-amber-700">
                  {warning}
                </p>
              ))}
              {result.errors.map((error) => (
                <p key={error} className="text-caption text-critical">
                  {error}
                </p>
              ))}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
