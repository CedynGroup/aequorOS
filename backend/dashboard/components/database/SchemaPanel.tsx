'use client';

/**
 * Discover Schema panel: the source schema pulled from a live introspection —
 * tables with their columns and a few sample values per column, to inform the
 * per-institution mapping. Read-only; nothing here persists.
 */

import type { DatabaseConnectionDiscoverResult } from '@aequoros/risk-service-api';
import { fmtLocale } from '@/lib/format';

export default function SchemaPanel({
  result,
}: {
  result: DatabaseConnectionDiscoverResult;
}) {
  const tables = result.tables ?? [];

  if (tables.length === 0) {
    return (
      <div className="rounded border border-border p-4 bg-surface-alt">
        <p className="text-body text-slate">
          The introspection returned no tables. Check the scoped schemas on this
          connection.
        </p>
      </div>
    );
  }

  const totalColumns = tables.reduce(
    (sum, table) => sum + (table.columns?.length ?? 0),
    0,
  );

  return (
    <div className="rounded border border-border bg-surface-alt p-4 space-y-3">
      <p className="text-body text-navy">
        <span className="font-mono font-medium">{tables.length}</span> table
        {tables.length === 1 ? '' : 's'} ·{' '}
        <span className="font-mono font-medium">{totalColumns}</span> columns discovered.
        Use these to map the source onto the canonical model.
      </p>
      <div className="space-y-3">
        {tables.map((table) => (
          <details
            key={table.name}
            className="rounded border border-border-light bg-surface"
          >
            <summary className="cursor-pointer select-none px-4 py-2.5 flex flex-wrap items-center gap-3">
              <span className="font-mono text-body text-navy">{table.name}</span>
              <span className="text-caption text-slate">
                {table.columns?.length ?? 0} column
                {(table.columns?.length ?? 0) === 1 ? '' : 's'}
              </span>
              {table.rowCount != null && (
                <span className="ml-auto text-caption font-mono text-slate tabular-nums">
                  {Number(table.rowCount).toLocaleString(fmtLocale())} rows
                </span>
              )}
            </summary>
            <div className="border-t border-border-light overflow-x-auto">
              <table className="w-full text-caption">
                <thead>
                  <tr className="text-left text-slate">
                    <th className="px-4 py-2 font-medium">Column</th>
                    <th className="px-4 py-2 font-medium">Sample values</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-light">
                  {(table.columns ?? []).map((column) => (
                    <tr key={column.name}>
                      <td className="px-4 py-2 font-mono text-navy align-top whitespace-nowrap">
                        {column.name}
                      </td>
                      <td className="px-4 py-2 font-mono text-slate">
                        {(column.sampleValues ?? []).length > 0
                          ? (column.sampleValues ?? []).join(', ')
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        ))}
      </div>
    </div>
  );
}
