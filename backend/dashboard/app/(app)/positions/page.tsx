'use client';

/**
 * Positions blotter — the canonical position book behind every module
 * calculation, as a desk-style table with server pagination. Filters (type,
 * currency, reference search) run server-side, so six-figure books page in
 * ~100-row windows; facets power the dropdowns and KPIs without ever reading
 * the whole book. Page, size, and filters sync to the URL for deep links.
 * Balances stay in their position currency (no cross-currency aggregation is
 * invented).
 */

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { ChevronLeft, ChevronRight, Layers, Search } from 'lucide-react';
import type { CanonicalPositionRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary from '@/components/ui/QueryBoundary';
import { SkeletonTable } from '@/components/ui/Skeleton';
import { useBankContext } from '@/components/shell/BankContext';
import {
  useCanonicalPositionFacets,
  useCanonicalPositionsPage,
} from '@/lib/api/hooks';
import { fmtDateUTC, labelize } from '@/lib/api/values';
import PositionDrawer, {
  fmtBalance,
  fmtRate,
  validationTone,
} from '@/components/positions/PositionDrawer';

const PAGE_SIZES = [100, 250, 500] as const;
const DEFAULT_PAGE_SIZE = 100;
const SEARCH_DEBOUNCE_MS = 300;
const ALL = 'all';

const fmtCount = (value: number) => value.toLocaleString('en-US');

/** Clamp a ?limit= value to the supported page sizes. */
function parseLimit(raw: string | null): number {
  const parsed = Number(raw);
  return (PAGE_SIZES as readonly number[]).includes(parsed)
    ? parsed
    : DEFAULT_PAGE_SIZE;
}

/** Parse a non-negative integer URL param, defaulting to 0. */
function parseOffset(raw: string | null): number {
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 0;
}

export default function PositionsBlotterPage() {
  // useSearchParams requires a Suspense boundary in the app router.
  return (
    <Suspense>
      <PositionsBlotter />
    </Suspense>
  );
}

function PositionsBlotter() {
  const { bank } = useBankContext();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  // URL is the source of truth for filters + paging (deep-linkable views).
  const typeFilter = searchParams.get('type') ?? ALL;
  const currencyFilter = searchParams.get('ccy') ?? ALL;
  const q = searchParams.get('q') ?? '';
  const limit = parseLimit(searchParams.get('limit'));
  const offset = parseOffset(searchParams.get('offset'));

  const setParams = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      if (value === null || value === '') next.delete(key);
      else next.set(key, value);
    }
    const qs = next.toString();
    router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  };

  // Search box: local echo of ?q= with a 300ms debounce before it hits the
  // server (every keystroke would otherwise be a filtered COUNT + page read).
  const [search, setSearch] = useState(q);
  const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => setSearch(q), [q]);
  const onSearchChange = (value: string) => {
    setSearch(value);
    if (debounce.current) clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      setParams({ q: value.trim() || null, offset: null });
    }, SEARCH_DEBOUNCE_MS);
  };
  useEffect(
    () => () => {
      if (debounce.current) clearTimeout(debounce.current);
    },
    []
  );

  const facets = useCanonicalPositionFacets(bank?.id);
  const page = useCanonicalPositionsPage(bank?.id, {
    limit,
    offset,
    positionType: typeFilter === ALL ? undefined : typeFilter,
    currency: currencyFilter === ALL ? undefined : currencyFilter,
    q,
  });

  const [selected, setSelected] = useState<CanonicalPositionRead | null>(null);

  const positions = useMemo(() => page.data?.positions ?? [], [page.data]);
  const total = page.data?.total ?? 0;
  const bookTotal = facets.data?.total ?? 0;
  const filtersActive =
    typeFilter !== ALL || currencyFilter !== ALL || q !== '';

  const columns: Column<CanonicalPositionRead>[] = [
    {
      key: 'ref',
      header: 'Reference',
      render: (p) => (
        <span className="font-mono text-caption text-navy">{p.sourceReference}</span>
      ),
      width: '18%',
    },
    { key: 'type', header: 'Type', render: (p) => labelize(p.positionType) },
    {
      key: 'ccy',
      header: 'Ccy',
      render: (p) => <span className="font-mono text-caption">{p.currency}</span>,
      width: '7%',
    },
    {
      key: 'balance',
      header: 'Balance (ccy)',
      numeric: true,
      render: (p) => fmtBalance(p.balance),
    },
    { key: 'rate', header: 'Rate', numeric: true, render: (p) => fmtRate(p.interestRate) },
    {
      key: 'maturity',
      header: 'Maturity',
      render: (p) => (
        <span className="font-mono text-caption">
          {p.contractualMaturity ? fmtDateUTC(p.contractualMaturity) : '—'}
        </span>
      ),
    },
    {
      key: 'source',
      header: 'Source',
      render: (p) => (
        <span className="inline-flex items-center px-1.5 py-0.5 rounded border border-border-light bg-surface text-[10px] font-mono uppercase tracking-wider text-slate">
          {p.sourceSystem}
        </span>
      ),
    },
    {
      key: 'quality',
      header: 'Quality',
      align: 'right',
      render: (p) => (
        <StatusPill tone={validationTone(p.validationStatus)}>
          {p.validationStatus}
        </StatusPill>
      ),
    },
  ];

  const selectClass =
    'px-2.5 py-2 text-caption font-medium bg-surface-raised border border-border rounded-md text-navy';
  const pagerButtonClass =
    'inline-flex items-center gap-1 px-2.5 py-1.5 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-40 disabled:pointer-events-none';

  const windowStart = total === 0 ? 0 : offset + 1;
  const windowEnd = Math.min(offset + positions.length, total);

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Markets' }, { label: 'Positions' }]}
        title="Positions"
        subtitle="The canonical position book behind every module calculation. Click a row for details and lineage back to its source batch."
        asOf={
          page.data?.asOfDate ? fmtDateUTC(page.data.asOfDate) : undefined
        }
      />

      <QueryBoundary
        isLoading={facets.isLoading || page.isLoading}
        error={facets.error ?? page.error}
        onRetry={() => {
          void facets.refetch();
          void page.refetch();
        }}
      >
        <div className="px-8 py-6 space-y-6">
          {facets.data && bookTotal === 0 ? (
            <EmptyState
              Icon={Layers}
              title="No canonical positions yet"
              description="Ingest a positions file (or push via the API) to populate the canonical model — the blotter fills in as batches land."
              action={
                <Link
                  href="/data-engine"
                  className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
                >
                  Open the Data Engine
                </Link>
              }
            />
          ) : (
            <>
              {/* Summary KPIs — facet counts span the whole current generation. */}
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                <KpiStat
                  label="Positions"
                  value={fmtCount(bookTotal)}
                  hint="current generation"
                />
                <KpiStat
                  label="Position types"
                  value={facets.data?.positionTypes.length ?? 0}
                  hint={facets.data?.positionTypes
                    .slice(0, 3)
                    .map((facet) => labelize(facet.value))
                    .join(' · ')}
                />
                <KpiStat
                  label="Currencies"
                  value={facets.data?.currencies.length ?? 0}
                  hint={facets.data?.currencies
                    .slice(0, 3)
                    .map((facet) => facet.value)
                    .join(' · ')}
                />
                <KpiStat
                  label="Matching filters"
                  value={fmtCount(total)}
                  hint={filtersActive ? 'server-filtered' : 'no filters applied'}
                />
              </div>

              {/* Filter bar — every control drives a server-side filter. */}
              <div className="flex items-center gap-3 flex-wrap">
                <label className="relative">
                  <Search
                    size={14}
                    className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-light"
                    aria-hidden
                  />
                  <input
                    type="search"
                    value={search}
                    onChange={(event) => onSearchChange(event.target.value)}
                    placeholder="Search reference…"
                    aria-label="Search by source reference"
                    maxLength={80}
                    className="pl-8 pr-3 py-2 w-56 text-caption bg-surface-raised border border-border rounded-md text-navy placeholder:text-slate-light"
                  />
                </label>
                <select
                  value={typeFilter}
                  onChange={(event) =>
                    setParams({
                      type: event.target.value === ALL ? null : event.target.value,
                      offset: null,
                    })
                  }
                  aria-label="Filter by position type"
                  className={selectClass}
                >
                  <option value={ALL}>All types</option>
                  {facets.data?.positionTypes.map((facet) => (
                    <option key={facet.value} value={facet.value}>
                      {labelize(facet.value)} ({fmtCount(facet.count)})
                    </option>
                  ))}
                </select>
                <select
                  value={currencyFilter}
                  onChange={(event) =>
                    setParams({
                      ccy: event.target.value === ALL ? null : event.target.value,
                      offset: null,
                    })
                  }
                  aria-label="Filter by currency"
                  className={selectClass}
                >
                  <option value={ALL}>All currencies</option>
                  {facets.data?.currencies.map((facet) => (
                    <option key={facet.value} value={facet.value}>
                      {facet.value} ({fmtCount(facet.count)})
                    </option>
                  ))}
                </select>
                {filtersActive && (
                  <button
                    type="button"
                    onClick={() =>
                      setParams({ type: null, ccy: null, q: null, offset: null })
                    }
                    className="text-caption font-medium text-action hover:underline"
                  >
                    Clear filters
                  </button>
                )}
              </div>

              {/* Blotter — previous page stays visible (dimmed) while the next loads. */}
              {page.isPending ? (
                <div className="card overflow-hidden">
                  <SkeletonTable rows={12} />
                </div>
              ) : total === 0 ? (
                <EmptyState
                  Icon={Layers}
                  title="No positions match the filters"
                  description="Clear the search or widen the type/currency filters."
                />
              ) : (
                <>
                  <div
                    className={`card overflow-hidden transition-opacity ${
                      page.isPlaceholderData ? 'opacity-50' : ''
                    }`}
                    aria-busy={page.isPlaceholderData}
                  >
                    <DataTable
                      columns={columns}
                      rows={positions}
                      density="compact"
                      stickyHeader
                      maxHeight="62vh"
                      onRowClick={(row) => setSelected(row)}
                    />
                  </div>

                  {/* Pagination footer */}
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <p className="text-caption text-slate">
                      Showing {fmtCount(windowStart)}–{fmtCount(windowEnd)} of{' '}
                      {fmtCount(total)} positions
                    </p>
                    <div className="flex items-center gap-2">
                      <label className="flex items-center gap-1.5 text-caption text-slate">
                        Rows
                        <select
                          value={limit}
                          onChange={(event) =>
                            setParams({
                              limit:
                                Number(event.target.value) === DEFAULT_PAGE_SIZE
                                  ? null
                                  : event.target.value,
                              offset: null,
                            })
                          }
                          aria-label="Rows per page"
                          className={selectClass}
                        >
                          {PAGE_SIZES.map((size) => (
                            <option key={size} value={size}>
                              {size}
                            </option>
                          ))}
                        </select>
                      </label>
                      <button
                        type="button"
                        onClick={() =>
                          setParams({
                            offset:
                              offset - limit > 0 ? String(offset - limit) : null,
                          })
                        }
                        disabled={offset === 0 || page.isPlaceholderData}
                        className={pagerButtonClass}
                      >
                        <ChevronLeft size={13} aria-hidden />
                        Prev
                      </button>
                      <button
                        type="button"
                        onClick={() => setParams({ offset: String(offset + limit) })}
                        disabled={
                          offset + limit >= total || page.isPlaceholderData
                        }
                        className={pagerButtonClass}
                      >
                        Next
                        <ChevronRight size={13} aria-hidden />
                      </button>
                    </div>
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </QueryBoundary>

      {selected && (
        <PositionDrawer position={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
