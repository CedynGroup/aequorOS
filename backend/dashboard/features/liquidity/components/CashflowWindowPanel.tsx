'use client';

/**
 * Contractual flows between dates — engine-backed cash-flow windows. The
 * backend buckets the current canonical book's contractual maturities into
 * per-currency calendar months (TIME-3); nothing is computed client-side.
 * Amounts render in each position's own currency (pill selector when the
 * window touches more than one), the base-equivalent totals in the footer
 * use only ingested conversion legs, and the null-maturity count keeps the
 * view honest about what a contractual window can never show.
 */

import { useState } from 'react';
import { CalendarRange } from 'lucide-react';
import type { CashflowWindowRead } from '@aequoros/risk-service-api';
import SectionCard from '@/components/ui/SectionCard';
import EmptyState from '@/components/ui/EmptyState';
import DataTable, { type Column } from '@/components/ui/DataTable';
import { ErrorPanel } from '@/components/ui/QueryBoundary';
import { SkeletonLine } from '@/components/ui/Skeleton';
import { useCashflowWindow } from '@/lib/api/hooks';
import { num } from '@/lib/api/values';
import { fmtCurrency, fmtCurrencySigned, fmtLocale } from '@/lib/format';

const DATE_INPUT_CLASSES =
  'border border-border bg-surface-raised rounded-md text-body font-mono px-2.5 py-1.5 text-navy';

type FlowRow = {
  label: string;
  currency: string;
  inflows: number;
  outflows: number;
  net: number;
  isTotal: boolean;
};

function monthLabel(month: Date): string {
  return month.toLocaleDateString(fmtLocale(), {
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}

function excludedLine(data: CashflowWindowRead): string {
  return data.noMaturityCount === 1
    ? '1 position carries no contractual maturity — excluded.'
    : `${data.noMaturityCount} positions carry no contractual maturity — excluded.`;
}

const columns: Column<FlowRow>[] = [
  {
    key: 'month',
    header: 'Month',
    width: '28%',
    render: (r) =>
      r.isTotal ? (
        <span className="font-medium text-navy">{r.label}</span>
      ) : (
        r.label
      ),
  },
  {
    key: 'inflows',
    header: 'Inflows',
    numeric: true,
    render: (r) => fmtCurrency(r.inflows, r.currency),
  },
  {
    key: 'outflows',
    header: 'Outflows',
    numeric: true,
    render: (r) => fmtCurrency(r.outflows, r.currency),
  },
  {
    key: 'net',
    header: 'Net',
    numeric: true,
    render: (r) => fmtCurrencySigned(r.net, r.currency),
  },
];

export default function CashflowWindowPanel({
  bankId,
}: {
  bankId: string | undefined;
}) {
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [applied, setApplied] = useState<{ start: string; end: string } | null>(
    null
  );
  const [selected, setSelected] = useState<string | null>(null);
  const query = useCashflowWindow(
    bankId,
    applied?.start,
    applied?.end,
    Boolean(applied)
  );

  const data = query.data;
  const currencies = (data?.totals ?? []).map((total) => total.currency);
  const active =
    selected && currencies.includes(selected) ? selected : currencies[0];
  const activeTotal = data?.totals.find((total) => total.currency === active);

  const rows: FlowRow[] = data
    ? [
        ...data.months
          .filter((row) => row.currency === active)
          .map((row) => ({
            label: monthLabel(row.month),
            currency: row.currency,
            inflows: num(row.inflows),
            outflows: num(row.outflows),
            net: num(row.net),
            isTotal: false,
          })),
        ...(activeTotal
          ? [
              {
                label: 'Total',
                currency: activeTotal.currency,
                inflows: num(activeTotal.inflows),
                outflows: num(activeTotal.outflows),
                net: num(activeTotal.net),
                isTotal: true,
              },
            ]
          : []),
      ]
    : [];

  return (
    <SectionCard
      title="Contractual flows between dates"
      subtitle="Maturities of the current canonical book inside the selected window — per currency, per calendar month, engine-computed"
      noPadding
      footer={
        data ? (
          <span>
            {excludedLine(data)}{' '}
            {data.positionCount > 0 && (
              <>
                Base-equivalent totals from ingested conversions: inflows{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(num(data.overall.inflows))}
                </span>{' '}
                · outflows{' '}
                <span className="font-mono text-navy">
                  {fmtCurrency(num(data.overall.outflows))}
                </span>{' '}
                · net{' '}
                <span className="font-mono text-navy">
                  {fmtCurrencySigned(num(data.overall.net))}
                </span>
                {data.overall.unconvertedCount > 0 && (
                  <>
                    {' '}
                    ({data.overall.unconvertedCount} position
                    {data.overall.unconvertedCount === 1 ? '' : 's'} without an
                    ingested conversion excluded)
                  </>
                )}
                .
              </>
            )}
          </span>
        ) : undefined
      }
    >
      <div className="p-5 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="date"
            value={start}
            onChange={(e) => setStart(e.target.value)}
            aria-label="Cash-flow window start date"
            className={DATE_INPUT_CLASSES}
          />
          <span className="text-caption text-slate">to</span>
          <input
            type="date"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
            aria-label="Cash-flow window end date"
            className={DATE_INPUT_CLASSES}
          />
          <button
            type="button"
            disabled={!start || !end || !bankId}
            onClick={() => setApplied({ start, end })}
            className="px-3 py-1.5 text-caption font-medium btn-primary disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Compute
          </button>
        </div>

        {!applied ? (
          <p className="text-caption text-slate">
            Pick a start and end date, then Compute — the engine aggregates the
            book&apos;s contractual maturities inside the window server-side.
          </p>
        ) : query.isFetching ? (
          <div className="space-y-2.5" aria-busy="true" aria-label="Computing">
            <SkeletonLine width="70%" height={12} />
            <SkeletonLine width="65%" height={12} />
            <SkeletonLine width="72%" height={12} />
          </div>
        ) : query.error ? (
          <ErrorPanel
            error={query.error}
            title="Could not compute the cash-flow window"
            onRetry={() => void query.refetch()}
          />
        ) : data && data.totals.length === 0 ? (
          <EmptyState
            Icon={CalendarRange}
            title="No contractual maturities in this window"
            description="No position of the current book matures between the selected dates. Positions without a contractual maturity never appear here — see the note below."
          />
        ) : data ? (
          <>
            {currencies.length > 1 && (
              <div className="flex items-center gap-2 flex-wrap">
                {currencies.map((currency) => {
                  const isActive = currency === active;
                  return (
                    <button
                      key={currency}
                      type="button"
                      onClick={() => setSelected(currency)}
                      className={`px-3 py-1.5 rounded-full text-caption font-medium border transition-colors ${
                        isActive
                          ? 'bg-action-light text-action border-action/30'
                          : 'bg-surface text-slate border-border hover:text-navy'
                      }`}
                    >
                      {currency}
                    </button>
                  );
                })}
              </div>
            )}
            <div className="-mx-5 -mb-5 border-t border-border-light">
              <DataTable
                columns={columns}
                rows={rows}
                density="compact"
                totalsRowMatcher={(row) => row.isTotal}
              />
            </div>
          </>
        ) : null}
      </div>
    </SectionCard>
  );
}
