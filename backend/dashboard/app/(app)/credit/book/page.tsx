'use client';

/**
 * The classified loan blotter: every loan behind the credit metrics, graded
 * under the tenant's own classification grid, filtered and paged server-side.
 * URL is the source of truth for filters (the /positions pattern).
 */

import { Suspense, useEffect, useMemo, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { BookOpenCheck, ChevronLeft, ChevronRight } from 'lucide-react';
import type { Column } from '@/components/ui/DataTable';
import type { CreditLoanRead } from '@aequoros/risk-service-api';
import DataTable from '@/components/ui/DataTable';
import EmptyState from '@/components/ui/EmptyState';
import KpiStat from '@/components/ui/KpiStat';
import PageHeader from '@/components/ui/PageHeader';
import QueryBoundary from '@/components/ui/QueryBoundary';
import SectionCard from '@/components/ui/SectionCard';
import StatusPill from '@/components/ui/StatusPill';
import { useBankContext } from '@/components/shell/BankContext';
import { useCreditLoanFacets, useCreditLoansPage } from '@/lib/api/hooks';
import { labelize, num } from '@/lib/api/values';
import { fmtCurrency, fmtInt } from '@/lib/format';

const PAGE_SIZES = [100, 250, 500];

function gradeTone(grade: string, nonPerforming: boolean): 'success' | 'amber' | 'critical' {
  if (nonPerforming) return 'critical';
  return grade === 'olem' ? 'amber' : 'success';
}

const columns: Column<CreditLoanRead>[] = [
  {
    key: 'ref',
    header: 'Reference',
    render: (r) => <span className="font-mono text-caption text-navy">{r.sourceReference}</span>,
  },
  {
    key: 'borrower',
    header: 'Borrower',
    render: (r) => <span className="text-caption text-navy/85">{r.counterpartyName ?? '—'}</span>,
  },
  {
    key: 'product',
    header: 'Product',
    render: (r) => (r.productCode ? labelize(r.productCode) : '—'),
  },
  {
    key: 'grade',
    header: 'Grade',
    render: (r) => (
      <StatusPill tone={gradeTone(r.grade, r.nonPerforming)}>{labelize(r.grade)}</StatusPill>
    ),
  },
  {
    key: 'dpd',
    header: 'DPD',
    align: 'right',
    numeric: true,
    render: (r) =>
      r.daysPastDue != null ? (
        fmtInt(r.daysPastDue)
      ) : (
        <span title="Classified via the IFRS 9 stage proxy">—</span>
      ),
  },
  {
    key: 'balance',
    header: 'Outstanding',
    align: 'right',
    numeric: true,
    render: (r) => fmtCurrency(num(r.exposureGhs)),
  },
  {
    key: 'provision',
    header: 'Provision required',
    align: 'right',
    numeric: true,
    render: (r) => fmtCurrency(num(r.provisionRequiredGhs)),
  },
  {
    key: 'held',
    header: 'Provision held',
    align: 'right',
    numeric: true,
    render: (r) => (r.provisionHeldGhs != null ? fmtCurrency(num(r.provisionHeldGhs)) : '—'),
  },
  {
    key: 'branch',
    header: 'Branch',
    render: (r) => r.branchId ?? '—',
  },
];

function LoanBookBody() {
  const router = useRouter();
  const params = useSearchParams();
  const { bank } = useBankContext();
  const bankId = bank?.id;

  const grade = params.get('grade') ?? undefined;
  const product = params.get('product') ?? undefined;
  const branch = params.get('branch') ?? undefined;
  const q = params.get('q') ?? '';
  const limit = Number(params.get('limit') ?? PAGE_SIZES[0]);
  const offset = Number(params.get('offset') ?? 0);

  const [search, setSearch] = useState(q);
  useEffect(() => setSearch(q), [q]);
  useEffect(() => {
    const handle = setTimeout(() => {
      if (search !== q) setParam('q', search || null, { resetOffset: true });
    }, 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  function setParam(key: string, value: string | null, opts?: { resetOffset?: boolean }) {
    const next = new URLSearchParams(params.toString());
    if (value === null || value === '') next.delete(key);
    else next.set(key, value);
    if (opts?.resetOffset) next.delete('offset');
    router.replace(`/credit/book?${next.toString()}`, { scroll: false });
  }

  const page = useCreditLoansPage(bankId, { limit, offset, grade, product, branch, q });
  const facets = useCreditLoanFacets(bankId);
  const rows = useMemo(() => page.data?.rows ?? [], [page.data]);
  const total = page.data?.total ?? 0;
  const filtered = page.data?.filtered ?? 0;

  const selectClass =
    'px-2.5 py-2 text-caption font-medium bg-surface-raised border border-border rounded-md text-navy';
  const pagerButtonClass =
    'inline-flex items-center gap-1 px-2.5 py-1.5 text-caption font-medium text-slate border border-border rounded-md hover:bg-surface disabled:opacity-40 disabled:pointer-events-none';

  const windowStart = filtered === 0 ? 0 : offset + 1;
  const windowEnd = Math.min(offset + rows.length, filtered);

  return (
    <>
      <PageHeader
        breadcrumbs={[
          { label: 'Modules', href: '/' },
          { label: 'Credit', href: '/credit' },
          { label: 'Loan Book' },
        ]}
        title="Loan Book"
        subtitle="Every loan behind the credit metrics, classified under the active grid."
        asOf={page.data?.asOf}
      />
      <QueryBoundary isLoading={page.isLoading} error={page.error} onRetry={() => page.refetch()}>
        {page.data && total === 0 ? (
          <div className="px-8 py-6">
            <EmptyState
              Icon={BookOpenCheck}
              title="No loans in the canonical book yet"
              description="Ingest the loan book through the Data Engine to populate the classified blotter."
              action={
                <a href="/data-engine" className="btn-primary">
                  Open the Data Engine
                </a>
              }
            />
          </div>
        ) : page.data ? (
          <div className="px-8 py-6 space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
              <KpiStat label="Loans on book" value={fmtInt(total)} hint="Current generation" />
              <KpiStat
                label="Matching filters"
                value={fmtInt(filtered)}
                hint={filtered === total ? 'No filters applied' : 'Server-filtered'}
              />
              <KpiStat
                label="Non-performing rows"
                value={fmtInt(rows.filter((r) => r.nonPerforming).length)}
                hint="On this page"
              />
              <KpiStat
                label="Page exposure"
                value={fmtCurrency(rows.reduce((sum, r) => sum + num(r.exposureGhs), 0))}
                hint="Sum of the visible rows"
              />
            </div>

            <div className="flex flex-wrap items-center gap-2">
              <input
                value={search}
                onChange={(event) => setSearch(event.target.value)}
                placeholder="Search reference or borrower…"
                className={`${selectClass} w-64`}
              />
              <select
                className={selectClass}
                value={grade ?? ''}
                onChange={(e) => setParam('grade', e.target.value || null, { resetOffset: true })}
              >
                <option value="">All grades</option>
                {(facets.data?.grades ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {labelize(f.value)} ({f.count})
                  </option>
                ))}
              </select>
              <select
                className={selectClass}
                value={product ?? ''}
                onChange={(e) => setParam('product', e.target.value || null, { resetOffset: true })}
              >
                <option value="">All products</option>
                {(facets.data?.products ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {labelize(f.value)} ({f.count})
                  </option>
                ))}
              </select>
              <select
                className={selectClass}
                value={branch ?? ''}
                onChange={(e) => setParam('branch', e.target.value || null, { resetOffset: true })}
              >
                <option value="">All branches</option>
                {(facets.data?.branches ?? []).map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.value} ({f.count})
                  </option>
                ))}
              </select>
            </div>

            <SectionCard title="Classified loans" noPadding>
              <div className={page.isFetching ? 'opacity-50' : ''} aria-busy={page.isFetching}>
                <DataTable
                  columns={columns}
                  rows={rows}
                  density="compact"
                  stickyHeader
                  maxHeight="62vh"
                />
              </div>
            </SectionCard>

            <div className="flex items-center justify-between">
              <p className="text-caption text-slate">
                Showing {fmtInt(windowStart)}–{fmtInt(windowEnd)} of {fmtInt(filtered)} loans
              </p>
              <div className="flex items-center gap-2">
                <select
                  className={selectClass}
                  value={String(limit)}
                  onChange={(e) => setParam('limit', e.target.value, { resetOffset: true })}
                >
                  {PAGE_SIZES.map((size) => (
                    <option key={size} value={size}>
                      {size} rows
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className={pagerButtonClass}
                  disabled={offset === 0}
                  onClick={() => setParam('offset', String(Math.max(0, offset - limit)))}
                >
                  <ChevronLeft size={14} /> Prev
                </button>
                <button
                  type="button"
                  className={pagerButtonClass}
                  disabled={offset + limit >= filtered}
                  onClick={() => setParam('offset', String(offset + limit))}
                >
                  Next <ChevronRight size={14} />
                </button>
              </div>
            </div>
          </div>
        ) : null}
      </QueryBoundary>
    </>
  );
}

export default function CreditLoanBookPage() {
  return (
    <Suspense>
      <LoanBookBody />
    </Suspense>
  );
}
