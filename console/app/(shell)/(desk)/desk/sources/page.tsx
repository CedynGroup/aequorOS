'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ExternalLink, PenLine } from 'lucide-react';
import { listDeskCaptures, type DeskCapture } from '@/lib/api';
import { useApi } from '@/lib/use-api';
import { fmtDate, fmtTs, relTime, DASH } from '@/lib/format';
import {
  Chip,
  type Column,
  CopyButton,
  DataTable,
  EmptyState,
  ErrorPanel,
  PageHeader,
  SectionCard,
  SkeletonRows,
  StatusChip,
} from '@/components/ui';
import { CaptureViewer } from '@/components/deskdata/CaptureViewer';

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

  // Latest capture per source, for the registry recency badges.
  const latestBySource = useMemo(() => {
    const map = new Map<string, string>();
    for (const c of data?.captures ?? []) {
      if (!map.has(c.source_key)) map.set(c.source_key, c.captured_at); // API order: newest first
    }
    return map;
  }, [data]);

  const columns = useMemo<Column<DeskCapture>[]>(
    () => [
      {
        key: 'source',
        header: 'Source',
        sortable: true,
        sortAccessor: (c) => c.source_key,
        render: (c) => {
          const info = SOURCE_NAMES.get(c.source_key);
          return (
            <div className="min-w-0">
              <div className="font-mono text-caption text-ink">{c.source_key}</div>
              {info && <div className="truncate text-micro text-slate">{info.name}</div>}
            </div>
          );
        },
      },
      {
        key: 'captured',
        header: 'Captured',
        sortable: true,
        sortAccessor: (c) => c.captured_at,
        render: (c) => (
          <span className="text-caption text-ink" title={fmtTs(c.captured_at)}>
            {relTime(c.captured_at)}
          </span>
        ),
      },
      {
        key: 'as_of',
        header: 'As of',
        sortable: true,
        sortAccessor: (c) => c.as_of_date,
        render: (c) => <span className="text-caption text-slate">{fmtDate(c.as_of_date)}</span>,
      },
      { key: 'status', header: 'Status', render: (c) => <StatusChip value={c.status} /> },
      {
        key: 'parser',
        header: 'Parser',
        render: (c) => <span className="font-mono text-micro text-slate">{c.parser_version}</span>,
      },
      {
        key: 'sha',
        header: 'Content SHA',
        render: (c) => (
          <span
            className="inline-flex items-center gap-0.5"
            onClick={(e) => e.stopPropagation()}
          >
            <span className="font-mono text-micro text-slate-light" title={c.content_sha256}>
              {c.content_sha256.slice(0, 12)}…
            </span>
            <CopyButton value={c.content_sha256} label="Copy content hash" />
          </span>
        ),
      },
      {
        key: 'issue',
        header: 'Issue',
        render: (c) =>
          c.parse_error ? (
            <Chip tone="crit" title={c.parse_error}>
              parse error
            </Chip>
          ) : (
            <span className="text-slate-light">{DASH}</span>
          ),
      },
      {
        key: 'url',
        header: '',
        align: 'right',
        render: (c) =>
          c.source_url ? (
            <a
              href={c.source_url}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="inline-flex items-center gap-1 text-micro text-action hover:underline"
            >
              <ExternalLink size={11} /> URL
            </a>
          ) : (
            <span className="text-slate-light">{DASH}</span>
          ),
      },
    ],
    [],
  );

  return (
    <div>
      <PageHeader
        title="Sources"
        sub="Tier-1 Ghana sources: HTML scrape, templated PDF/XLSX parse, and one real API — every one with a manual-entry fallback, because layouts drift and BoG blocks automation. Open any capture for field-level source review."
      />

      {/* ------------------------------------------------ registry cards.
          Static mirror of the backend SOURCE_REGISTRY (identity metadata). */}
      <div className="mb-8 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {FAMILIES.map((family) => (
          <SectionCard key={family.title} title={family.title} subtitle={family.blurb}>
            <ul className="space-y-2">
              {family.sources.map((s) => {
                const latest = latestBySource.get(s.key) ?? null;
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
          </SectionCard>
        ))}
      </div>

      <p className="mb-4 text-caption text-slate-light">
        Scheduled fetch triggers arrive with the scheduler phase — captures below were made by the
        ingestion tooling, and nothing on this page starts a pull.
      </p>

      {/* --------------------------------------------------- capture log */}
      <SectionCard title="Captures" subtitle="Raw source documents, newest first — click a row to inspect content." noPadding>
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
        {data && data.captures.length > 0 && (
          <DataTable
            columns={columns}
            rows={data.captures}
            density="compact"
            pageSize={20}
            getFilterText={(c) =>
              `${c.source_key} ${SOURCE_NAMES.get(c.source_key)?.name ?? ''} ${c.status} ${c.parser_version}`
            }
            filterPlaceholder="Filter captures by source, status, parser…"
            onRowClick={(c) => setViewerId(c.id)}
          />
        )}
      </SectionCard>

      <CaptureViewer captureId={viewerId} onClose={() => setViewerId(null)} />
    </div>
  );
}
