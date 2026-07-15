import type { ReactNode } from 'react';

type Align = 'left' | 'right' | 'center';

export type Column<T> = {
  key: string;
  header: ReactNode;
  align?: Align;
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
}: {
  columns: Column<T>[];
  rows: T[];
  density?: 'compact' | 'comfortable';
  emphasizeTotals?: boolean;
  totalsRowMatcher?: (row: T) => boolean;
  className?: string;
}) {
  const padY = density === 'compact' ? 'py-1.5' : 'py-2.5';

  return (
    <div className={`overflow-x-auto ${className}`}>
      <table className="w-full text-body border-collapse">
        <thead>
          <tr className="border-b border-border bg-surface">
            {columns.map((c) => (
              <th
                key={c.key}
                scope="col"
                style={{ width: c.width }}
                className={`${padY} px-4 text-micro font-medium uppercase tracking-wider text-slate ${
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
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const isTotal =
              emphasizeTotals && totalsRowMatcher ? totalsRowMatcher(row) : false;
            return (
              <tr
                key={i}
                className={`border-b border-border-light last:border-b-0 ${
                  isTotal ? 'bg-surface font-medium' : 'hover:bg-surface-alt'
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
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
