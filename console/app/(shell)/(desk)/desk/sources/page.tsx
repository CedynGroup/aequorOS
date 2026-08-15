'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ExternalLink, FileSearch, PenLine } from 'lucide-react';
import {
  getDeskCaptureContent,
  listDeskCaptures,
  toApiError,
  type ApiError,
  type DeskCapture,
  type DeskCaptureContentView,
} from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import { Chip, EmptyState, ErrorPanel, PageHeader, SkeletonRows, StatusChip } from '@/components/ui';

/**
 * /desk/sources — capture history per source plus the source registry.
 *
 * Captures come from GET /operator/v1/desk/captures. The registry cards are a
 * STATIC mirror of backend/app/services/market_desk/sources/__init__.py
 * SOURCE_REGISTRY (identity/cadence metadata, not data) — if a source is
 * added there, add its card here. Fetch-trigger buttons belong to the
 * scheduler phase and are deliberately absent: this page never pretends a
 * pull can be started from here.
 */

type Cadence = 'daily' | 'weekly' | 'monthly' | 'per_event';

interface SourceInfo {
  key: string;
  name: string;
  cadence: Cadence;
  method: string;
  /** Series-code prefix for the pre-filtered manual-entry link. */
  seriesPrefix: string;
}

interface SourceFamily {
  title: string;
  blurb: string;
  sources: SourceInfo[];
}

// Staleness thresholds by cadence — mirrors sources/core.py STALENESS_LIMIT_DAYS.
const STALENESS_LIMIT_DAYS: Record<Cadence, number> = {
  daily: 5,
  weekly: 14,
  monthly: 75,
  per_event: 120,
};

const FAMILIES: SourceFamily[] = [
  {
    title: 'BoG wpDataTables (Treasury & the Markets)',
    blurb:
      'HTML tables behind the BoG admin-ajax protocol — t-bill/BoG-bill tenders, interbank, MPR, FX, and the monthly matrix.',
    sources: [
      { key: 'bog_tbill_rates', name: 'Treasury bill / GoG tender rates (table 2)', cadence: 'weekly', method: 'wdt_ajax', seriesPrefix: 'GHS.TBILL.' },
      { key: 'bog_bill_rates', name: 'BoG bill tender rates (table 3)', cadence: 'weekly', method: 'wdt_ajax', seriesPrefix: 'GHS.BOGBILL.' },
      { key: 'bog_interbank_daily', name: 'Daily interbank weighted-average rate (table 69)', cadence: 'daily', method: 'wdt_ajax', seriesPrefix: 'GHS.INTERBANK.ON' },
      { key: 'bog_interbank_weekly', name: 'Weekly average interbank rate (table 70)', cadence: 'weekly', method: 'wdt_ajax', seriesPrefix: 'GHS.INTERBANK.WAVG' },
      { key: 'bog_mpr', name: 'MPC policy rate (table 62)', cadence: 'per_event', method: 'wdt_ajax', seriesPrefix: 'GHS.MPR' },
      { key: 'bog_fx_daily', name: 'Daily interbank FX rates (table 31)', cadence: 'daily', method: 'wdt_ajax', seriesPrefix: 'GHS.FX.' },
      { key: 'bog_fx_historical', name: 'Historical interbank FX rates (table 40)', cadence: 'daily', method: 'wdt_ajax', seriesPrefix: 'GHS.FX.' },
      { key: 'bog_fx_reference', name: 'FX market reference rate banner (table 32)', cadence: 'daily', method: 'wdt_ajax', seriesPrefix: 'GHS.FX.USDGHS.REF' },
      { key: 'bog_econ_interest_monthly', name: 'Monthly interest-rate matrix (table 21)', cadence: 'monthly', method: 'wdt_ajax', seriesPrefix: 'GHS.ECONDATA.' },
    ],
  },
  {
    title: 'BoG auction result notices',
    blurb: 'Weekly GoG and BoG securities auction results — templated PDF parse.',
    sources: [
      { key: 'bog_gog_auction_pdf', name: 'GoG securities auction result notice', cadence: 'weekly', method: 'pdf', seriesPrefix: 'GHS.AUCTION.GOG.' },
      { key: 'bog_bog_auction_pdf', name: 'BoG bill auction result notice', cadence: 'weekly', method: 'pdf', seriesPrefix: 'GHS.AUCTION.BOG.' },
    ],
  },
  {
    title: 'BoG APR of banks',
    blurb:
      'Monthly bank-by-bank lending APRs — the official public source for competitor visibility (spec §12).',
    sources: [
      { key: 'bog_apr_pdf', name: 'Annual Percentage Rates of banks (monthly PDF)', cadence: 'monthly', method: 'pdf', seriesPrefix: 'GHS.APR.' },
    ],
  },
  {
    title: 'BoG Summary of Economic & Financial Data',
    blurb: 'The bimonthly SEFD PDF — fixed table-of-contents layout, templated parse (§3a/3b).',
    sources: [
      { key: 'bog_sefd_pdf', name: 'Summary of Economic and Financial Data', cadence: 'monthly', method: 'pdf', seriesPrefix: 'GHS.SEFD.' },
    ],
  },
  {
    title: 'GFIM secondary market',
    blurb: 'Daily trading report workbooks and the monthly status report (actual/364 conventions).',
    sources: [
      { key: 'gfim_daily_xlsx', name: 'Daily trading report (XLSX)', cadence: 'daily', method: 'xlsx', seriesPrefix: 'GHS.GFIM.' },
      { key: 'gfim_monthly_status', name: 'Monthly status report (PDF extract)', cadence: 'monthly', method: 'pdf', seriesPrefix: 'GHS.GFIM.MONTHLY.' },
    ],
  },
  {
    title: 'GSS statsbank',
    blurb:
      'PxWeb API for GRR / IWAL / lending-rate series — the closest thing to an API in the Ghana stack, but stale.',
    sources: [
      { key: 'gss_interest_px', name: 'Statsbank interest rates (PxWeb)', cadence: 'monthly', method: 'pxweb_api', seriesPrefix: 'GHS.GRR' },
    ],
  },
];

const SOURCE_NAMES = new Map(
  FAMILIES.flatMap((f) => f.sources.map((s) => [s.key, s] as const)),
);

function RecencyBadge({ latest, cadence }: { latest: string | null; cadence?: Cadence }) {
  if (!latest) return <Chip>never captured</Chip>;
  const ageDays = (Date.now() - Date.parse(latest)) / 86_400_000;
  const limit = cadence ? STALENESS_LIMIT_DAYS[cadence] : null;
  const stale = limit !== null && Number.isFinite(ageDays) && ageDays > limit;
  return (
    <Chip tone={stale ? 'warn' : 'ok'} title={fmtTs(latest)}>
      last capture {relTime(latest)}
    </Chip>
  );
}

export default function SourcesPage() {
  const { data, error, loading, reload } = useApi(() => listDeskCaptures());
  const [viewerId, setViewerId] = useState<string | null>(null);
  const [viewer, setViewer] = useState<DeskCaptureContentView | null>(null);
  const [viewerError, setViewerError] = useState<ApiError | null>(null);
  const [viewerBusy, setViewerBusy] = useState(false);
  const [needle, setNeedle] = useState('');

  const grouped = useMemo(() => {
    const map = new Map<string, DeskCapture[]>();
    for (const c of data?.captures ?? []) {
      const bucket = map.get(c.source_key) ?? [];
      bucket.push(c); // API order: newest first
      map.set(c.source_key, bucket);
    }
    return map;
  }, [data]);

  async function openViewer(captureId: string, search?: string) {
    setViewerId(captureId);
    setViewerBusy(true);
    setViewerError(null);
    try {
      const view = await getDeskCaptureContent(captureId, search || undefined);
      setViewer(view);
    } catch (err) {
      setViewerError(toApiError(err));
      setViewer(null);
    }
    setViewerBusy(false);
  }

  return (
    <div>
      <PageHeader
        title="Sources"
        sub="Tier-1 Ghana sources: HTML scrape, templated PDF/XLSX parse, and one real API — every one with a manual-entry fallback, because layouts drift and BoG blocks automation. Open capture content for field-level source review."
      />

      {/* ------------------------------------------------ registry cards */}
      <div className="mb-8 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {FAMILIES.map((family) => (
          <div key={family.title} className="card p-4">
            <h2 className="text-body font-medium text-navy">{family.title}</h2>
            <p className="mt-1 text-caption text-slate">{family.blurb}</p>
            <ul className="mt-3 space-y-2">
              {family.sources.map((s) => {
                const captures = grouped.get(s.key);
                const latest = captures?.[0]?.captured_at ?? null;
                return (
                  <li key={s.key} className="rounded border border-border-light bg-surface p-2.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="min-w-0 flex-1 text-caption text-ink">{s.name}</span>
                      <Chip mono title={`fetch method: ${s.method}`}>
                        {s.cadence.replace('_', ' ')}
                      </Chip>
                    </div>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <RecencyBadge latest={latest} cadence={s.cadence} />
                      <Link
                        href={`/desk/observations?entry=1&series=${encodeURIComponent(s.seriesPrefix)}`}
                        className="inline-flex items-center gap-1 text-micro font-medium text-action hover:underline"
                        title="Every source has a manual-entry fallback by design (spec §3)"
                      >
                        <PenLine size={11} /> manual fallback available
                      </Link>
                    </div>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <p className="mb-4 text-caption text-slate-light">
        Scheduled fetch triggers arrive with the scheduler phase — captures below were made by
        the ingestion tooling, and nothing on this page starts a pull.
      </p>

      {/* --------------------------------------------------- capture log */}
      <div className="card">
        <h2 className="border-b border-border-light px-5 py-3 text-h3 text-navy">Captures</h2>
        {loading && <SkeletonRows rows={6} />}
        {error && (
          <div className="p-4">
            <ErrorPanel error={error} onRetry={reload} context="Loading captures" />
          </div>
        )}
        {data && data.captures.length === 0 && (
          <EmptyState
            title="No captures yet"
            hint="Raw source documents (BoG pages, auction PDFs, GFIM workbooks) appear here once the ingestion tooling captures them. Until then, the manual-entry fallback is the way data enters."
          />
        )}
        {data &&
          [...grouped.entries()].map(([sourceKey, captures]) => {
            const info = SOURCE_NAMES.get(sourceKey);
            return (
              <div key={sourceKey} className="border-b border-border-light last:border-b-0">
                <div className="flex flex-wrap items-center gap-2 bg-surface px-5 py-2">
                  <span className="font-mono text-caption text-ink">{sourceKey}</span>
                  {info && <span className="text-caption text-slate">{info.name}</span>}
                  <span className="text-micro text-slate-light">
                    {captures.length} capture{captures.length === 1 ? '' : 's'}
                  </span>
                  <span className="ml-auto">
                    <RecencyBadge
                      latest={captures[0]?.captured_at ?? null}
                      cadence={info?.cadence}
                    />
                  </span>
                </div>
                <table className="w-full text-body">
                  <tbody>
                    {captures.map((c) => (
                      <tr key={c.id} className="border-b border-border-light last:border-b-0">
                        <td className="px-5 py-2 text-caption text-ink" title={fmtTs(c.captured_at)}>
                          {relTime(c.captured_at)}
                        </td>
                        <td className="px-3 py-2 text-caption text-slate">
                          as of {fmtDate(c.as_of_date)}
                        </td>
                        <td className="px-3 py-2">
                          <StatusChip value={c.status} />
                        </td>
                        <td className="px-3 py-2 font-mono text-micro text-slate">
                          {c.parser_version}
                        </td>
                        <td
                          className="max-w-[10rem] truncate px-3 py-2 font-mono text-micro text-slate-light"
                          title={c.content_sha256}
                        >
                          {c.content_sha256.slice(0, 12)}…
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex flex-wrap items-center gap-2">
                            <button
                              type="button"
                              onClick={() => void openViewer(c.id)}
                              className="inline-flex items-center gap-1 text-micro font-medium text-action hover:underline"
                            >
                              <FileSearch size={11} /> view content
                            </button>
                            {c.source_url ? (
                              <a
                                href={c.source_url}
                                target="_blank"
                                rel="noreferrer"
                                className="inline-flex items-center gap-1 text-micro text-action hover:underline"
                              >
                                <ExternalLink size={11} /> URL
                              </a>
                            ) : null}
                          </div>
                        </td>
                        <td className="max-w-md break-words px-5 py-2 text-caption text-critical">
                          {c.parse_error ?? ''}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          })}
      </div>

      {/* Capture content / snippet viewer */}
      {viewerId && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-navy/40 p-4">
          <div className="card max-h-[90vh] w-full max-w-3xl overflow-hidden flex flex-col">
            <div className="flex items-start justify-between gap-3 border-b border-border-light px-5 py-3">
              <div>
                <h2 className="text-h3 text-navy">Capture content</h2>
                <p className="font-mono text-micro text-slate">{viewerId}</p>
              </div>
              <button
                type="button"
                className="text-caption text-slate hover:text-ink"
                onClick={() => {
                  setViewerId(null);
                  setViewer(null);
                  setNeedle('');
                }}
              >
                Close
              </button>
            </div>
            <div className="flex flex-wrap items-end gap-2 border-b border-border-light px-5 py-3">
              <label className="block min-w-[12rem] flex-1">
                <span className="mb-1 block text-caption font-medium text-slate">
                  Find value (snippet)
                </span>
                <input
                  value={needle}
                  onChange={(e) => setNeedle(e.target.value)}
                  className="w-full rounded-md border border-border bg-surface-base px-3 py-2 font-mono text-body text-ink focus:border-focus focus:outline-none"
                  placeholder="e.g. 15.00"
                />
              </label>
              <button
                type="button"
                className="btn-primary px-3 py-2 text-body"
                onClick={() => void openViewer(viewerId, needle)}
              >
                Search
              </button>
            </div>
            <div className="overflow-y-auto px-5 py-4">
              {viewerBusy && <p className="text-caption text-slate">Loading…</p>}
              {viewerError && <ErrorPanel error={viewerError} context="Loading capture content" />}
              {viewer && (
                <div className="space-y-3">
                  <div className="flex flex-wrap gap-2">
                    <Chip mono>{viewer.source_key}</Chip>
                    <Chip>{viewer.kind}</Chip>
                    <Chip tone={viewer.content_available ? 'ok' : 'warn'}>
                      {viewer.content_available
                        ? `${viewer.content_bytes} bytes`
                        : 'content not inline'}
                    </Chip>
                    {viewer.truncated && <Chip tone="warn">truncated</Chip>}
                  </div>
                  {viewer.source_url && (
                    <a
                      href={viewer.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-caption text-action hover:underline"
                    >
                      {viewer.source_url}
                    </a>
                  )}
                  {viewer.content_omitted && (
                    <p className="text-caption text-warning">{viewer.content_omitted}</p>
                  )}
                  {viewer.snippet && (
                    <div>
                      <h3 className="text-body font-medium text-navy">Snippet around value</h3>
                      <pre className="mt-1 whitespace-pre-wrap rounded border border-action/30 bg-action-light/30 p-3 font-mono text-caption text-ink">
                        {viewer.snippet}
                      </pre>
                    </div>
                  )}
                  {viewer.text != null && (
                    <div>
                      <h3 className="text-body font-medium text-navy">Full text</h3>
                      <pre className="mt-1 max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border-light bg-surface p-3 font-mono text-micro text-ink">
                        {viewer.text}
                      </pre>
                    </div>
                  )}
                  {!viewer.content_available && !viewer.text && (
                    <p className="text-caption text-slate">
                      Raw bytes not stored inline (over size cap). Use the source URL or re-capture
                      with a smaller artifact.
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
