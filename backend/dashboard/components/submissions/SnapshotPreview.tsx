/**
 * Read-only preview of a regulatory-package-v1 snapshot: headline totals plus
 * one DataTable per section (rows + cross-footed totals row). Columns are
 * derived from the snapshot rows themselves, so every generator's sections
 * render without per-return layout code. Values are shown in raw GHS units —
 * the exported artifacts render amounts in GHS '000 per the BoG convention.
 */

import DataTable, { type Column } from '@/components/ui/DataTable';
import { labelize } from '@/lib/api/values';
import { regShort } from '@/lib/format';

type SnapshotRow = Record<string, unknown>;

type SnapshotSection = {
  code?: string;
  title?: string;
  optional?: boolean;
  rows?: SnapshotRow[];
  total?: SnapshotRow | null;
  /** The sheet's official reporting unit — values are already scaled to it. */
  unit?: string;
};

// The snapshot value is pre-scaled to the sheet's official reporting unit
// (bog_forms scale_for_export), which varies by sheet — BSD balance-sheet cells
// are in millions, others in thousands. Label it honestly so a preparer never
// misreads the magnitude.
//
// TWO VOCABULARIES reach this component. Template-driven BSD sections carry the
// backend's `UnitConvention` on the SECTION (millions / thousands / units /
// percent / count / text). The generic generators carry no section unit at all,
// but their ROWS may carry `unit: "pct"` (`generation.py::_row(..., unit="pct")`).
// Both are mapped here; a unit this map does not know is shown verbatim rather
// than dropped.
const UNIT_LABELS: Record<string, string> = {
  millions: 'GHS millions',
  thousands: "GHS '000",
  units: 'GHS',
  // The generic generators' own spelling for whole currency units
  // (`snapshot_row(..., unit="ghs")` in dbk/le/lrt_generation). Unmapped it fell
  // through to the verbatim branch and a preparer read "in ghs" under a
  // regulatory figure — a lower-cased raw payload value, not a currency code.
  ghs: 'GHS',
  percent: '%',
  pct: '%',
  count: 'count',
};

function unitLabel(unit: unknown): string | null {
  if (typeof unit !== 'string' || unit === '' || unit === 'text') return null;
  return UNIT_LABELS[unit] ?? unit;
}

/** True for the percent flavours — the only unit rendered as a value suffix. */
function isPercentUnit(unit: unknown): boolean {
  return unit === 'pct' || unit === 'percent';
}

type Snapshot = Record<string, unknown> & {
  sections?: SnapshotSection[];
  totals?: SnapshotRow[];
};

// Row keys that are structure or template/provenance METADATA — never a data
// column. `code`/`description`/`value`/`unit` are structural; the rest are the
// per-line metadata the template-based BoG forms attach in
// `bog_forms/generation.py::build_sections` (cell address, day column, mapping
// status, source, notes, scaling flag). Without excluding them the generic
// preview renders the Excel cell addresses as columns and buries the Value —
// which is exactly the "preview shows Excel cells, not the return" bug.
// Genuine data dimensions (e.g. `previous_month_ghs`) are NOT listed, so they
// still render as columns.
const NON_DATA_KEYS = new Set([
  'code',
  'description',
  'value',
  'unit',
  'equals_sum_of_rows',
  'cell',
  'column',
  'unscaled',
  'status',
  'source',
  'notes',
  '__isTotal',
]);

/** Format a snapshot cell: numbers with separators + parenthesised negatives
 * (BoG sign convention), booleans as Yes/No, everything else verbatim.
 *
 * Exported because the version comparison shows the same cells side by side:
 * a figure that reads one way in the preview and another in the diff would put
 * the operator in the position of deciding which rendering to believe. */
export function fmtSnapshotCell(value: unknown): string {
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
      if (NON_DATA_KEYS.has(key)) continue;
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
        render: (row) => fmtSnapshotCell(row[key]),
      })
    ),
    {
      key: 'value',
      header: 'Value',
      numeric: true,
      // A row that declares itself a percentage says so on the figure. Without
      // this a ratio row rendered as a bare "14.2" beside currency rows.
      render: (row) =>
        `${fmtSnapshotCell(row.value)}${isPercentUnit(row.unit) && row.value !== null && row.value !== undefined && row.value !== '' ? '%' : ''}`,
    },
  ];
  return columns;
}

/**
 * The unit preamble, scoped to what this snapshot ACTUALLY labels.
 *
 * Only the template-driven BSD family emits a per-section unit
 * (`bog_forms/generation.py` sets `"unit": sheet.unit`); the generic
 * `_section()` emits no unit key at all. Asserting "shown per section below"
 * for every family put a preparer in front of unlabelled figures under a
 * sentence promising the units were labelled. The promise is now derived from
 * the payload instead of assumed.
 */
function unitPreamble(sections: SnapshotSection[]): string {
  const negatives = `Negative values are parenthesised per the ${regShort()} convention — identical to the exported artifacts.`;
  if (sections.length === 0) return negatives;
  const labelled = sections.filter((s) => unitLabel(s.unit) !== null).length;
  if (labelled === sections.length) {
    return `Values are in each sheet's official reporting unit, shown per section below. ${negatives}`;
  }
  if (labelled > 0) {
    return `Values are in each section's official reporting unit. ${labelled} of ${sections.length} sections declare that unit and show it below; the rest carry the return's own unit, which this generator does not declare — confirm it against the exported artifact before certifying. ${negatives}`;
  }
  return `Values are in the return's own reporting unit. This generator does not declare a per-section unit, so none is shown here — confirm the unit against the exported artifact before certifying. ${negatives}`;
}

export default function SnapshotPreview({ snapshot }: { snapshot: Snapshot }) {
  const sections = snapshot.sections ?? [];
  const totals = snapshot.totals ?? [];

  return (
    <div className="space-y-5">
      <p className="text-caption text-slate">{unitPreamble(sections)}</p>

      {/* Three across, not four. A package total is a full GHS amount —
          1,961,000,000 is thirteen mono glyphs — and in a quarter of this card
          it was rendered as "1,961,000,…". A regulatory figure that is silently
          cut is worse than one that takes a second line, so the tiles are wider
          and the value wraps instead of truncating; the label still truncates
          but now carries its full text as a tooltip. */}
      {totals.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {totals.map((total, i) => (
            <div
              key={String(total.code ?? i)}
              className="rounded border border-border-light bg-surface px-3 py-2.5 min-w-0"
            >
              <p
                className="text-micro font-medium text-slate uppercase tracking-wider truncate"
                title={String(total.description ?? total.code ?? 'Total')}
              >
                {String(total.description ?? total.code ?? 'Total')}
              </p>
              <p className="mt-1 font-mono text-h3 text-navy tnum break-words">
                {fmtSnapshotCell(total.value)}
                {/* Accepts BOTH unit vocabularies: the check used to test only
                    'pct' while the section map keyed on 'percent', so a headline
                    ratio lost its % under either spelling. */}
                {isPercentUnit(total.unit) ? '%' : ''}
              </p>
              {unitLabel(total.unit) && !isPercentUnit(total.unit) && (
                <p className="mt-0.5 text-micro text-slate">
                  in {unitLabel(total.unit)}
                </p>
              )}
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
              <div className="flex items-baseline gap-2 min-w-0">
                <p className="text-body font-medium text-navy truncate">
                  {String(section.title ?? section.code ?? `Section ${i + 1}`)}
                </p>
                {unitLabel(section.unit) && (
                  <span className="text-caption text-slate whitespace-nowrap">
                    · in {unitLabel(section.unit)}
                  </span>
                )}
              </div>
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
