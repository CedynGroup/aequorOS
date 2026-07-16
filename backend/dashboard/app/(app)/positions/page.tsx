'use client';

/**
 * Positions blotter — the canonical position book behind every module
 * calculation, as a desk-style table with filters and a lineage-backed
 * detail drawer. The canonical-positions endpoint has no server pagination,
 * so rendering is capped client-side with an explicit note; balances stay in
 * their position currency (no cross-currency aggregation is invented).
 */

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { Database, Layers, Search } from 'lucide-react';
import type { CanonicalPositionRead } from '@aequoros/risk-service-api';
import PageHeader from '@/components/ui/PageHeader';
import KpiStat from '@/components/ui/KpiStat';
import StatusPill from '@/components/ui/StatusPill';
import DataTable, { type Column } from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import QueryBoundary, { ErrorPanel } from '@/components/ui/QueryBoundary';
import { useBankContext } from '@/components/shell/BankContext';
import {
  isPositionReadTimeout,
  useCanonicalPositionsBlotter,
} from '@/lib/api/hooks';
import { useIngestionSummary } from '@/lib/api/ingestion';
import { fmtDateUTC, labelize } from '@/lib/api/values';
import PositionDrawer, {
  fmtBalance,
  fmtRate,
  validationTone,
} from '@/components/positions/PositionDrawer';

/** Client-side render cap — the endpoint exposes no server pagination. */
const MAX_ROWS = 500;

/**
 * Books beyond this size are never fetched: the unpaginated endpoint
 * serializes the whole current generation, and a six-figure book ties up the
 * backend for minutes. The ingestion summary's canonical count gates the read.
 */
const MAX_FETCHABLE_POSITIONS = 20_000;

const ALL = 'all';

export default function PositionsBlotterPage() {
  const { bank } = useBankContext();
  const summary = useIngestionSummary(bank?.id);
  const positionCount = summary.data?.canonicalCounts.positions;
  const tooLarge =
    positionCount !== undefined && positionCount > MAX_FETCHABLE_POSITIONS;
  // Fetch only once the summary proves the book is a fetchable size.
  const positionsQuery = useCanonicalPositionsBlotter(
    positionCount !== undefined && !tooLarge ? bank?.id : undefined
  );
  const positions = useMemo(
    () => positionsQuery.data?.positions ?? [],
    [positionsQuery.data]
  );

  const [typeFilter, setTypeFilter] = useState(ALL);
  const [currencyFilter, setCurrencyFilter] = useState(ALL);
  const [search, setSearch] = useState('');
  const [selected, setSelected] = useState<CanonicalPositionRead | null>(null);

  const types = useMemo(
    () => [...new Set(positions.map((p) => p.positionType))].sort(),
    [positions]
  );
  const currencies = useMemo(
    () => [...new Set(positions.map((p) => p.currency))].sort(),
    [positions]
  );

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return positions.filter(
      (p) =>
        (typeFilter === ALL || p.positionType === typeFilter) &&
        (currencyFilter === ALL || p.currency === currencyFilter) &&
        (needle === '' || p.sourceReference.toLowerCase().includes(needle))
    );
  }, [positions, typeFilter, currencyFilter, search]);

  const visible = filtered.slice(0, MAX_ROWS);
  const flagged = positions.filter((p) => p.validationStatus !== 'accepted').length;

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

  return (
    <>
      <PageHeader
        breadcrumbs={[{ label: 'Markets' }, { label: 'Positions' }]}
        title="Positions"
        subtitle="The canonical position book behind every module calculation. Click a row for details and lineage back to its source batch."
        asOf={
          positionsQuery.data?.asOfDate
            ? fmtDateUTC(positionsQuery.data.asOfDate)
            : undefined
        }
      />

      {summary.isError ? (
        <div className="px-8 py-6">
          <ErrorPanel
            error={summary.error}
            onRetry={() => void summary.refetch()}
            title="Could not size the position book"
          />
        </div>
      ) : tooLarge ? (
        <div className="px-8 py-6">
          <EmptyState
            Icon={Database}
            title="Position book too large for a single read"
            description={`This bank's current generation holds ${positionCount.toLocaleString(
              'en-US'
            )} canonical positions, and the canonical-positions endpoint has no server pagination — serializing that in one response would tie up the risk service for minutes, so the read is not attempted. Browse positions per ingestion batch in the Data Engine instead.`}
            action={
              <Link
                href="/data-engine"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
              >
                Open the Data Engine
              </Link>
            }
          />
        </div>
      ) : isPositionReadTimeout(positionsQuery.error) ? (
        <div className="px-8 py-6">
          <EmptyState
            Icon={Database}
            title="Positions read timed out"
            description="The canonical-positions endpoint has no server pagination and this book could not be serialized within 30 seconds. Browse positions per ingestion batch in the Data Engine instead."
            action={
              <Link
                href="/data-engine"
                className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary"
              >
                Open the Data Engine
              </Link>
            }
          />
        </div>
      ) : (
      <QueryBoundary
        isLoading={summary.isLoading || positionsQuery.isLoading}
        error={positionsQuery.error}
        onRetry={() => positionsQuery.refetch()}
      >
        {positionsQuery.data && (
          <div className="px-8 py-6 space-y-6">
            {positions.length === 0 ? (
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
                {/* Summary KPIs */}
                <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">
                  <KpiStat
                    label="Positions"
                    value={positions.length.toLocaleString('en-US')}
                    hint="current generation"
                  />
                  <KpiStat
                    label="Position types"
                    value={types.length}
                    hint={types.slice(0, 3).map(labelize).join(' · ')}
                  />
                  <KpiStat
                    label="Currencies"
                    value={currencies.length}
                    hint={currencies.join(' · ')}
                  />
                  <KpiStat
                    label="Data quality flags"
                    value={flagged.toLocaleString('en-US')}
                    status={flagged > 0 ? 'warn' : 'ok'}
                    hint="rows not in accepted status"
                  />
                </div>

                {/* Filter bar */}
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
                      onChange={(event) => setSearch(event.target.value)}
                      placeholder="Search reference…"
                      aria-label="Search by source reference"
                      className="pl-8 pr-3 py-2 w-56 text-caption bg-surface-raised border border-border rounded-md text-navy placeholder:text-slate-light"
                    />
                  </label>
                  <select
                    value={typeFilter}
                    onChange={(event) => setTypeFilter(event.target.value)}
                    aria-label="Filter by position type"
                    className={selectClass}
                  >
                    <option value={ALL}>All types</option>
                    {types.map((type) => (
                      <option key={type} value={type}>
                        {labelize(type)}
                      </option>
                    ))}
                  </select>
                  <select
                    value={currencyFilter}
                    onChange={(event) => setCurrencyFilter(event.target.value)}
                    aria-label="Filter by currency"
                    className={selectClass}
                  >
                    <option value={ALL}>All currencies</option>
                    {currencies.map((currency) => (
                      <option key={currency} value={currency}>
                        {currency}
                      </option>
                    ))}
                  </select>
                  <span className="text-caption text-slate">
                    {filtered.length.toLocaleString('en-US')} of{' '}
                    {positions.length.toLocaleString('en-US')} positions
                  </span>
                </div>

                {/* Blotter */}
                {filtered.length === 0 ? (
                  <EmptyState
                    Icon={Layers}
                    title="No positions match the filters"
                    description="Clear the search or widen the type/currency filters."
                  />
                ) : (
                  <div className="card overflow-hidden">
                    <DataTable
                      columns={columns}
                      rows={visible}
                      density="compact"
                      stickyHeader
                      maxHeight="62vh"
                      onRowClick={(row) => setSelected(row)}
                    />
                  </div>
                )}
                {filtered.length > MAX_ROWS && (
                  <p className="text-caption text-slate">
                    Showing the first {MAX_ROWS.toLocaleString('en-US')} of{' '}
                    {filtered.length.toLocaleString('en-US')} matching positions —
                    the canonical-positions endpoint has no server pagination, so
                    refine the filters to narrow the set.
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </QueryBoundary>
      )}

      {selected && (
        <PositionDrawer position={selected} onClose={() => setSelected(null)} />
      )}
    </>
  );
}
