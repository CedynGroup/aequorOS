'use client';

import { Chip, DataTable, type Column } from '@/components/ui';
import type { DeskCurveBlock, DeskCurvePoint } from '@/lib/api';
import { asRecord, show } from './util';

/**
 * One constructed curve block: identity chips, the tenor/zero grid as a dense
 * DataTable (scrolls past a few dozen pillars), a build-error line when the
 * solve failed, and any synthetic-discounting disclosure.
 */
export function CurveCard({ code, block }: { code: string; block: DeskCurveBlock }) {
  const interpolation = asRecord(block.definition)?.interpolation;
  const points = block.points ?? [];

  const columns: Column<DeskCurvePoint>[] = [
    {
      key: 'tenor',
      header: 'Tenor (m)',
      numeric: true,
      sortable: true,
      sortAccessor: (p) => p.tenor_months ?? null,
      render: (p) => show(p.tenor_months),
    },
    { key: 'zero', header: 'Zero %', numeric: true, render: (p) => show(p.rate_pct) },
  ];

  return (
    <div className="card overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-border-light px-4 py-3">
        <Chip mono>{code}</Chip>
        {block.curve_type && (
          <span className="text-micro uppercase tracking-wide text-slate">{block.curve_type}</span>
        )}
        {typeof interpolation === 'string' && (
          <span className="font-mono text-micro text-slate-light">{interpolation}</span>
        )}
      </div>
      {block.build_error ? (
        <p className="px-4 py-3 text-caption text-critical">{block.build_error}</p>
      ) : points.length > 0 ? (
        <DataTable columns={columns} rows={points} density="compact" stickyHeader maxHeight={280} />
      ) : (
        <p className="px-4 py-3 text-caption text-slate">No curve points.</p>
      )}
      {block.disclosure && (
        <p className="border-t border-border-light px-4 py-2 text-caption text-slate">
          {block.disclosure}
        </p>
      )}
    </div>
  );
}
