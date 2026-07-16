import type { KeyboardEvent, ReactNode } from 'react';
import { ChevronRight } from 'lucide-react';

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
}) {
  const padY = density === 'compact' ? 'py-1.5' : 'py-2.5';
  const clickable = Boolean(onRowClick);

  const handleKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, row: T, i: number) => {
    if (!onRowClick) return;
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowClick(row, i);
    }
  };

  return (
    <div
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
  );
}
