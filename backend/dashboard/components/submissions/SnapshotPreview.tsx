/**
 * Read-only preview of a regulatory-package-v1 snapshot: headline totals plus
 * one DataTable per section (rows + cross-footed totals row). Columns are
 * derived from the snapshot rows themselves, so every generator's sections
 * render without per-return layout code. Values are shown in raw GHS units —
 * the exported artifacts render amounts in GHS '000 per the BoG convention.
 */

import DataTable, { type Column } from '@/components/ui/DataTable';
import { labelize } from '@/lib/api/values';

type SnapshotRow = Record<string, unknown>;

type SnapshotSection = {
  code?: string;
  title?: string;
  optional?: boolean;
  rows?: SnapshotRow[];
  total?: SnapshotRow | null;
};

type Snapshot = Record<string, unknown> & {
  sections?: SnapshotSection[];
  totals?: SnapshotRow[];
};

const RESERVED_KEYS = new Set(['code', 'description', 'unit', 'equals_sum_of_rows']);

/** Format a snapshot cell: numbers with separators + parenthesised negatives
 * (BoG sign convention), booleans as Yes/No, everything else verbatim. */
function fmtCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  const parsed = Number(value);
  if (typeof value !== 'object' && value !== '' && Number.isFinite(parsed)) {
    const formatted = Math.abs(parsed).toLocaleString('en-GB', {
      maximumFractionDigits: 2,
    });
    return parsed < 0 ? `(${formatted})` : formatted;
  }
  return String(value);
}

/** Stable extra-column order: first-seen across the section's rows. */
function extraKeys(rows: SnapshotRow[]): string[] {
  const keys: string[] = [];
  for (const row of rows) {
    for (const key of Object.keys(row)) {
      if (RESERVED_KEYS.has(key) || key === 'value') continue;
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys;
}

function sectionColumns(rows: SnapshotRow[]): Column<SnapshotRow>[] {
  const columns: Column<SnapshotRow>[] = [
    {
      key: 'code',
      header: 'Row',
      render: (row) => (
        <span className="font-mono text-caption text-slate whitespace-nowrap">
          {String(row.code ?? '')}
        </span>
      ),
    },
    {
      key: 'description',
      header: 'Item',
      render: (row) => (
        <span className="text-navy/90">{String(row.description ?? '')}</span>
      ),
    },
    ...extraKeys(rows).map(
      (key): Column<SnapshotRow> => ({
        key,
        header: labelize(key),
        numeric: true,
        render: (row) => fmtCell(row[key]),
      })
    ),
    {
      key: 'value',
      header: 'Value',
      numeric: true,
      render: (row) => fmtCell(row.value),
    },
  ];
  return columns;
}

export default function SnapshotPreview({ snapshot }: { snapshot: Snapshot }) {
  const sections = snapshot.sections ?? [];
  const totals = snapshot.totals ?? [];

  return (
    <div className="space-y-5">
      <p className="text-caption text-slate">
        Values shown in GHS units; exported artifacts render amounts in
        GHS&nbsp;&apos;000 with parenthesised negatives (BoG convention).
      </p>

      {totals.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {totals.map((total, i) => (
            <div
              key={String(total.code ?? i)}
              className="rounded border border-border-light bg-surface px-3 py-2.5 min-w-0"
            >
              <p className="text-micro font-medium text-slate uppercase tracking-wider truncate">
                {String(total.description ?? total.code ?? 'Total')}
              </p>
              <p className="mt-1 font-mono text-h3 text-navy tnum truncate">
                {fmtCell(total.value)}
                {total.unit === 'pct' ? '%' : ''}
              </p>
            </div>
          ))}
        </div>
      )}

      {sections.map((section, i) => {
        const rows = section.rows ?? [];
        const withTotal = section.total
          ? [...rows, { ...section.total, __isTotal: true }]
          : rows;
        return (
          <div
            key={String(section.code ?? i)}
            className="rounded border border-border-light overflow-hidden"
          >
            <div className="flex items-center justify-between gap-3 px-4 py-2.5 bg-surface border-b border-border-light">
              <p className="text-body font-medium text-navy">
                {String(section.title ?? section.code ?? `Section ${i + 1}`)}
              </p>
              <span className="font-mono text-micro text-slate uppercase tracking-wider">
                {String(section.code ?? '')}
              </span>
            </div>
            <DataTable
              columns={sectionColumns(rows)}
              rows={withTotal}
              density="compact"
              totalsRowMatcher={(row) => Boolean(row.__isTotal)}
            />
          </div>
        );
      })}
    </div>
  );
}
