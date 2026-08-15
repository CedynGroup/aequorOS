'use client';

import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from 'react';
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search } from 'lucide-react';

type Align = 'left' | 'right' | 'center';

export type Column<T> = {
  key: string;
  header: ReactNode;
  align?: Align;
  /** Right-aligns and renders the cell in tabular-numeral mono (`.num`). */
  numeric?: boolean;
  width?: string;
  render: (row: T, idx: number) => ReactNode;
  /** Enables the header sort control; needs `sortAccessor` (or falls back to render text). */
  sortable?: boolean;
  /** Comparable value for sorting this column. */
  sortAccessor?: (row: T) => string | number | null | undefined;
};

type SortState = { key: string; dir: 'asc' | 'desc' } | null;

/**
 * The console's one table primitive. Ports the dashboard's DataTable (columns
 * with numeric/align/render, sticky header, density, onRowClick + keyboard)
 * and adds client-side sort, a filter box, and pagination — so feature pages
 * stop hand-rolling `<table>`s.
 */
export function DataTable<T>({
  columns,
  rows,
  density = 'comfortable',
  emphasizeTotals = true,
  totalsRowMatcher,
  className = '',
  stickyHeader = false,
  maxHeight,
  onRowClick,
  rowClassName,
  initialSort = null,
  getFilterText,
  filterPlaceholder = 'Filter…',
  pageSize,
  emptyMessage = 'No rows.',
}: {
  columns: Column<T>[];
  rows: T[];
  density?: 'compact' | 'comfortable';
  emphasizeTotals?: boolean;
  totalsRowMatcher?: (row: T) => boolean;
  className?: string;
  /** Keeps the header row pinned while the body scrolls (pair with maxHeight). */
  stickyHeader?: boolean;
  /** Constrains the scroll container height, e.g. 420 or '60vh'. */
  maxHeight?: number | string;
  /** Makes rows interactive: pointer cursor, hover, chevron affordance. */
  onRowClick?: (row: T, idx: number) => void;
  rowClassName?: (row: T, idx: number) => string;
  /** Initial sort; header clicks take over from here. */
  initialSort?: SortState;
  /** Provide to render a filter box; returns the searchable text for a row. */
  getFilterText?: (row: T) => string;
  filterPlaceholder?: string;
  /** When set, paginate at this page size. */
  pageSize?: number;
  emptyMessage?: ReactNode;
}) {
  const padY = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const clickable = Boolean(onRowClick);

  const [sort, setSort] = useState<SortState>(initialSort);
  const [query, setQuery] = useState('');
  const [page, setPage] = useState(0);

  const columnByKey = useMemo(() => {
    const map = new Map<string, Column<T>>();
    for (const c of columns) map.set(c.key, c);
    return map;
  }, [columns]);

  const filtered = useMemo(() => {
    if (!getFilterText || !query.trim()) return rows;
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    return rows.filter((r) => {
      const hay = getFilterText(r).toLowerCase();
      return terms.every((t) => hay.includes(t));
    });
  }, [rows, query, getFilterText]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columnByKey.get(sort.key);
    const accessor = col?.sortAccessor;
    if (!accessor) return filtered;
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...filtered].sort((a, b) => {
      const av = accessor(a);
      const bv = accessor(b);
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1;
      if (bv === null || bv === undefined) return -1;
      if (typeof av === 'number' && typeof bv === 'number') return (av - bv) * dir;
      return String(av).localeCompare(String(bv), undefined, { numeric: true }) * dir;
    });
  }, [filtered, sort, columnByKey]);

  const pageCount = pageSize ? Math.max(1, Math.ceil(sorted.length / pageSize)) : 1;

  // Reset to the first page whenever the visible set changes shape.
  useEffect(() => {
    setPage(0);
  }, [query, sort, pageSize, rows]);

  const visible = pageSize ? sorted.slice(page * pageSize, page * pageSize + pageSize) : sorted;

  const toggleSort = (key: string) => {
    setSort((prev) => {
      if (!prev || prev.key !== key) return { key, dir: 'asc' };
      if (prev.dir === 'asc') return { key, dir: 'desc' };
      return null;
    });
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, row: T, i: number) => {
    if (!onRowClick) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowClick(row, i);
    }
  };

  return (
    <div className={className}>
      {getFilterText && (
        <div className="flex items-center gap-2 border-b border-border-light px-4 py-2.5">
          <Search size={14} className="shrink-0 text-slate" aria-hidden />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={filterPlaceholder}
            className="w-full bg-transparent text-body text-navy placeholder:text-slate outline-none"
          />
          {query && (
            <span className="shrink-0 text-caption text-slate tnum">
              {sorted.length}/{rows.length}
            </span>
          )}
        </div>
      )}

      <div
        className={`overflow-x-auto ${maxHeight !== undefined ? 'overflow-y-auto' : ''}`}
        style={maxHeight !== undefined ? { maxHeight } : undefined}
      >
        <table className="w-full border-collapse text-body tnum">
          <thead>
            <tr className="border-b border-border bg-surface">
              {columns.map((c) => {
                const isSorted = sort?.key === c.key;
                const alignRight = c.align === 'right' || c.numeric;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    style={{ width: c.width }}
                    className={`${padY} px-4 text-micro font-medium uppercase tracking-wider text-slate ${
                      stickyHeader ? 'sticky top-0 z-10 bg-surface' : ''
                    } ${alignRight ? 'text-right' : c.align === 'center' ? 'text-center' : 'text-left'}`}
                  >
                    {c.sortable && c.sortAccessor ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(c.key)}
                        className={`inline-flex items-center gap-1 uppercase tracking-wider transition-colors hover:text-navy ${
                          alignRight ? 'flex-row-reverse' : ''
                        } ${isSorted ? 'text-navy' : ''}`}
                      >
                        {c.header}
                        {isSorted ? (
                          sort?.dir === 'asc' ? (
                            <ArrowUp size={11} aria-hidden />
                          ) : (
                            <ArrowDown size={11} aria-hidden />
                          )
                        ) : (
                          <ArrowUp size={11} className="opacity-25" aria-hidden />
                        )}
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
              {clickable && (
                <th
                  scope="col"
                  aria-label="Open"
                  className={`${padY} w-8 px-2 ${stickyHeader ? 'sticky top-0 z-10 bg-surface' : ''}`}
                />
              )}
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 ? (
              <tr>
                <td
                  colSpan={columns.length + (clickable ? 1 : 0)}
                  className="px-4 py-10 text-center text-caption text-slate"
                >
                  {emptyMessage}
                </td>
              </tr>
            ) : (
              visible.map((row, i) => {
                const isTotal =
                  emphasizeTotals && totalsRowMatcher ? totalsRowMatcher(row) : false;
                return (
                  <tr
                    key={i}
                    onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                    onKeyDown={onRowClick ? (e) => handleKeyDown(e, row, i) : undefined}
                    tabIndex={clickable ? 0 : undefined}
                    className={`group border-b border-border-light last:border-b-0 ${
                      isTotal ? 'bg-surface font-medium' : 'hover:bg-surface'
                    } ${clickable ? 'cursor-pointer' : ''} ${
                      rowClassName ? rowClassName(row, i) : ''
                    }`}
                  >
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={`${padY} px-4 align-middle ${c.numeric ? 'num' : ''} ${
                          c.align === 'right' && !c.numeric
                            ? 'text-right'
                            : c.align === 'center'
                            ? 'text-center'
                            : ''
                        } ${isTotal ? 'text-navy' : 'text-navy/90'}`}
                      >
                        {c.render(row, i)}
                      </td>
                    ))}
                    {clickable && (
                      <td className={`${padY} px-2 text-right align-middle`}>
                        <ChevronRight
                          size={14}
                          className="inline-block text-slate-light transition-colors group-hover:text-action"
                          aria-hidden
                        />
                      </td>
                    )}
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      {pageSize && sorted.length > pageSize && (
        <div className="flex items-center justify-between gap-3 border-t border-border-light px-4 py-2.5 text-caption text-slate">
          <span className="tnum">
            {page * pageSize + 1}–{Math.min((page + 1) * pageSize, sorted.length)} of {sorted.length}
          </span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              className="inline-flex items-center gap-1 rounded border border-border-light px-2 py-1 hover:bg-surface disabled:opacity-40"
            >
              <ChevronLeft size={13} aria-hidden /> Prev
            </button>
            <span className="px-1 tnum">
              {page + 1}/{pageCount}
            </span>
            <button
              type="button"
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
              className="inline-flex items-center gap-1 rounded border border-border-light px-2 py-1 hover:bg-surface disabled:opacity-40"
            >
              Next <ChevronRight size={13} aria-hidden />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
