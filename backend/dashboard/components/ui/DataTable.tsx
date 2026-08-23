'use client';

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { ChevronRight, MoveHorizontal } from 'lucide-react';

type Align = 'left' | 'right' | 'center';

export type Column<T> = {
  key: string;
  header: ReactNode;
  align?: Align;
  /** Right-aligns and renders the cell in tabular-numeral mono (`.num`). */
  numeric?: boolean;
  width?: string;
  render: (row: T, idx: number) => ReactNode;
};

/**
 * Dense data table with a MEASURED horizontal-scroll affordance.
 *
 * The container has always been `overflow-x-auto`, so a table wider than its
 * card has always scrolled — silently. On a 1280px viewport that meant the FTP
 * Line P&L lost "Implied margin (view)", "Net contribution" and "Below floor"
 * off the right-hand edge of a half-width card, and Product Profitability cut
 * "CONTRIBUTION" mid-word at the card border, with nothing on screen saying
 * there was more. A column a reader cannot see and cannot discover is a column
 * that is not published.
 *
 * So overflow is now measured (`ResizeObserver` + the scroll position) and,
 * only when it exists, the table gains: a fade over whichever edge still has
 * content, a keyboard-reachable scroll region, and a one-line caption naming
 * the gesture. Tables that fit render exactly as before — no fade, no caption,
 * no tab stop. This is the shared fix for every table in the package, which is
 * why it lives here and not on a page: the two FTP tables are the instances
 * that were caught, not the extent of the problem.
 */
export default function DataTable<T>({
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
  scrollLabel = 'Table',
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
  /** Names the scroll region for screen readers when the table overflows. */
  scrollLabel?: string;
}) {
  const padY = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const clickable = Boolean(onRowClick);

  const scrollRef = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: false, end: false });

  // 1px of tolerance: sub-pixel layout routinely leaves scrollWidth a fraction
  // above clientWidth on a table that visibly fits, and a permanent fade on a
  // table with nothing hidden is its own small lie.
  const measure = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const remaining = el.scrollWidth - el.clientWidth - el.scrollLeft;
    setEdges((current) => {
      const next = { start: el.scrollLeft > 1, end: remaining > 1 };
      return current.start === next.start && current.end === next.end
        ? current
        : next;
    });
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return undefined;
    measure();
    if (typeof ResizeObserver === 'undefined') return undefined;
    // Watch the container AND the table: the container changes with the
    // viewport, the table with the data.
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    const table = el.firstElementChild;
    if (table) observer.observe(table);
    return () => observer.disconnect();
  }, [measure, columns.length, rows.length]);

  const overflows = edges.start || edges.end;

  const handleKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, row: T, i: number) => {
    if (!onRowClick) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowClick(row, i);
    }
  };

  const fade = (side: 'left' | 'right') => (
    <div
      className={`pointer-events-none absolute inset-y-0 ${side}-0 w-10 z-20`}
      style={{
        background: `linear-gradient(to ${side === 'left' ? 'right' : 'left'}, rgb(var(--surface-raised)), rgb(var(--surface-raised) / 0))`,
      }}
      aria-hidden
    />
  );

  return (
    <div className="relative">
      <div
        ref={scrollRef}
        onScroll={measure}
        role={overflows ? 'region' : undefined}
        aria-label={overflows ? `${scrollLabel}, scrollable horizontally` : undefined}
        tabIndex={overflows ? 0 : undefined}
        className={`overflow-x-auto ${maxHeight !== undefined ? 'overflow-y-auto' : ''} ${className}`}
        style={maxHeight !== undefined ? { maxHeight } : undefined}
      >
        <table className="w-full text-body border-collapse tnum">
          <thead>
            <tr className="border-b border-border bg-surface">
              {columns.map((c) => (
                <th
                  key={c.key}
                  scope="col"
                  style={{ width: c.width }}
                  className={`${padY} px-4 text-micro font-medium uppercase tracking-wider text-slate ${
                    stickyHeader ? 'sticky top-0 z-10 bg-surface' : ''
                  } ${
                    c.align === 'right' || c.numeric
                      ? 'text-right'
                      : c.align === 'center'
                      ? 'text-center'
                      : 'text-left'
                  }`}
                >
                  {c.header}
                </th>
              ))}
              {clickable && (
                <th
                  scope="col"
                  aria-label="Open"
                  className={`${padY} px-2 w-8 ${
                    stickyHeader ? 'sticky top-0 z-10 bg-surface' : ''
                  }`}
                />
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => {
              const isTotal =
                emphasizeTotals && totalsRowMatcher ? totalsRowMatcher(row) : false;
              return (
                <tr
                  key={i}
                  onClick={onRowClick ? () => onRowClick(row, i) : undefined}
                  onKeyDown={
                    onRowClick ? (e) => handleKeyDown(e, row, i) : undefined
                  }
                  tabIndex={clickable ? 0 : undefined}
                  className={`border-b border-border-light last:border-b-0 group ${
                    isTotal ? 'bg-surface font-medium' : 'hover:bg-surface'
                  } ${clickable ? 'cursor-pointer' : ''} ${
                    rowClassName ? rowClassName(row, i) : ''
                  }`}
                >
                  {columns.map((c) => (
                    <td
                      key={c.key}
                      className={`${padY} px-4 align-middle ${
                        c.numeric ? 'num' : ''
                      } ${
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
                    <td className={`${padY} px-2 align-middle text-right`}>
                      <ChevronRight
                        size={14}
                        className="inline-block text-slate-light group-hover:text-action transition-colors"
                        aria-hidden
                      />
                    </td>
                  )}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {edges.start && fade('left')}
      {edges.end && fade('right')}
      {overflows && (
        <p className="flex items-center gap-1.5 px-4 pt-2 text-caption text-slate">
          <MoveHorizontal size={13} className="shrink-0" aria-hidden />
          More columns to the {edges.end ? 'right' : 'left'} — scroll sideways to
          read them.
        </p>
      )}
    </div>
  );
}
