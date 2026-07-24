'use client';

/**
 * Bulk CSV import for the Institution Profile registers (related parties,
 * outlets).
 *
 * DELIBERATE DESIGN — honest client-side bulk: the file is parsed and
 * validated in the browser (enum columns against the generated contract
 * values) and each valid row replays through the SAME reasoned single-row
 * register mutations the manual forms use (createRelatedParty /
 * createOutlet), every write carrying the audit reason
 * "Bulk CSV import: <filename>". Nothing is imported while any row is
 * invalid. Bulk server-side ingestion via the Data Engine (mappings +
 * lineage) is the future pathway for large registers; this stays a
 * convenience layered over the canonical endpoints, not a new write path.
 */

import { useRef, useState } from 'react';
import { Download, FileUp, Loader2, Upload, X } from 'lucide-react';
import SectionCard from '@/components/ui/SectionCard';
import { downloadTextFile } from '@/lib/download';
import { templateCsv, type RegisterTemplate } from '@/lib/templates';

/** Row verdict from the page-supplied validator. */
export type CsvRowResult<TPayload> =
  | { ok: true; payload: TPayload }
  | { ok: false; errors: string[] };

type RowError = { line: number; errors: string[] };
type RowFailure = { line: number; message: string };
type ParsedRow<TPayload> = { line: number; payload: TPayload };

const MAX_LISTED_ERRORS = 20;

/** Strict YYYY-MM-DD: right shape AND a real calendar date (UTC-parsed). */
export function isIsoCsvDate(value: string): boolean {
  return (
    /^\d{4}-\d{2}-\d{2}$/.test(value) &&
    !Number.isNaN(Date.parse(`${value}T00:00:00Z`))
  );
}

/**
 * Simple split-based CSV parse — deliberately no quoted-comma support (the
 * register templates carry no commas in cells; a cell containing a comma is
 * reported as a column-count error, never silently mis-imported). Cells are
 * trimmed, blank lines skipped, 1-based file line numbers kept for errors.
 */
function parseCsvLines(text: string): { line: number; cells: string[] }[] {
  return text
    .split(/\r\n|\r|\n/)
    .map((raw, index) => ({ line: index + 1, raw: raw.trim() }))
    .filter(({ raw }) => raw.length > 0)
    .map(({ line, raw }) => ({
      line,
      cells: raw.split(',').map((cell) => cell.trim()),
    }));
}

export default function CsvImport<TPayload>({
  entityLabel,
  template,
  disabled = false,
  parseRow,
  importRow,
  onFinished,
}: {
  /** Plural noun for copy, e.g. "related parties". */
  entityLabel: string;
  template: RegisterTemplate;
  disabled?: boolean;
  /** Validate one padded data row (cells match template.columns order). */
  parseRow: (cells: string[]) => CsvRowResult<TPayload>;
  /** One reasoned single-row write (the existing create mutation). */
  importRow: (payload: TPayload, reason: string) => Promise<unknown>;
  /** Called once after the run (refresh the composed register read). */
  onFinished: () => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [filename, setFilename] = useState<string | null>(null);
  const [headerError, setHeaderError] = useState<string | null>(null);
  const [rowErrors, setRowErrors] = useState<RowError[]>([]);
  const [rows, setRows] = useState<ParsedRow<TPayload>[]>([]);
  const [importing, setImporting] = useState(false);
  const [imported, setImported] = useState(0);
  const [failures, setFailures] = useState<RowFailure[]>([]);
  const [done, setDone] = useState(false);

  const reset = () => {
    setFilename(null);
    setHeaderError(null);
    setRowErrors([]);
    setRows([]);
    setImporting(false);
    setImported(0);
    setFailures([]);
    setDone(false);
  };

  const handleFile = async (file: File) => {
    reset();
    setFilename(file.name);
    const parsed = parseCsvLines(await file.text());
    if (parsed.length === 0) {
      setHeaderError('The file is empty.');
      return;
    }
    const [header, ...data] = parsed;
    if (
      header.cells.map((cell) => cell.toLowerCase()).join(',') !==
      template.columns.join(',')
    ) {
      setHeaderError(
        `The header row must be exactly "${template.columns.join(',')}" ` +
          `(got "${header.cells.join(',')}"). Download the template to start ` +
          'from the expected layout.'
      );
      return;
    }
    if (data.length === 0) {
      setHeaderError('No data rows found below the header.');
      return;
    }
    const errors: RowError[] = [];
    const valid: ParsedRow<TPayload>[] = [];
    for (const { line, cells } of data) {
      if (cells.length > template.columns.length) {
        errors.push({
          line,
          errors: [
            `expected ${template.columns.length} columns, found ${cells.length} ` +
              '(cells containing commas are not supported by the bulk import)',
          ],
        });
        continue;
      }
      const padded = cells.concat(
        Array<string>(template.columns.length - cells.length).fill('')
      );
      const result = parseRow(padded);
      if (result.ok) {
        valid.push({ line, payload: result.payload });
      } else {
        errors.push({ line, errors: result.errors });
      }
    }
    setRowErrors(errors);
    // All-or-nothing gate: any invalid row keeps the whole file out.
    if (errors.length === 0) setRows(valid);
  };

  const runImport = async () => {
    if (!filename || rows.length === 0) return;
    setImporting(true);
    const reason = `Bulk CSV import: ${filename}`;
    const collected: RowFailure[] = [];
    let count = 0;
    // Sequential on purpose — one reasoned write per row, in file order, so
    // partial failures leave an unambiguous "first N rows" state.
    for (const { line, payload } of rows) {
      try {
        await importRow(payload, reason);
        count += 1;
        setImported(count);
      } catch (error) {
        collected.push({
          line,
          message: error instanceof Error ? error.message : 'Request failed.',
        });
        setFailures([...collected]);
      }
    }
    onFinished();
    setImporting(false);
    setDone(true);
  };

  const invalid = headerError !== null || rowErrors.length > 0;
  const smallBtn =
    'inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium text-navy border border-border rounded-md hover:bg-surface disabled:opacity-60';

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          className="hidden"
          aria-hidden
          tabIndex={-1}
          onChange={(e) => {
            const file = e.target.files?.[0];
            // Allow re-selecting the same file after a fix.
            e.target.value = '';
            if (file) void handleFile(file);
          }}
        />
        <button
          type="button"
          disabled={disabled || importing}
          onClick={() => inputRef.current?.click()}
          className={smallBtn}
        >
          <Upload size={13} aria-hidden />
          Import CSV
        </button>
        <button
          type="button"
          onClick={() =>
            downloadTextFile(
              template.filename,
              templateCsv(template),
              'text/csv;charset=utf-8'
            )
          }
          className="inline-flex items-center gap-1 text-caption font-medium text-action hover:underline"
        >
          <Download size={12} aria-hidden />
          Download template
        </button>
        <span className="text-micro text-slate">
          {template.filename} — rows import one by one through the audited
          register endpoints.
        </span>
      </div>

      {filename && invalid && (
        <SectionCard
          title={`Cannot import ${filename}`}
          subtitle={`Fix every row and re-select the file — nothing was imported (${entityLabel} are all-or-nothing)`}
          actions={
            <button type="button" onClick={reset} className={smallBtn}>
              <X size={13} aria-hidden />
              Dismiss
            </button>
          }
        >
          {headerError ? (
            <p className="text-caption text-critical leading-relaxed">
              {headerError}
            </p>
          ) : (
            <ul className="space-y-1">
              {rowErrors.slice(0, MAX_LISTED_ERRORS).map((entry) => (
                <li
                  key={entry.line}
                  className="text-caption text-critical leading-relaxed"
                >
                  Line {entry.line}: {entry.errors.join('; ')}
                </li>
              ))}
              {rowErrors.length > MAX_LISTED_ERRORS && (
                <li className="text-caption text-slate">
                  … and {rowErrors.length - MAX_LISTED_ERRORS} more invalid rows.
                </li>
              )}
            </ul>
          )}
        </SectionCard>
      )}

      {filename && !invalid && rows.length > 0 && !done && (
        <SectionCard
          title={
            importing
              ? `Importing ${entityLabel} — imported ${imported} of ${rows.length}`
              : `Ready to import ${rows.length} row${rows.length === 1 ? '' : 's'} of ${entityLabel} from ${filename}`
          }
          subtitle={`Each row calls the audited single-row endpoint with reason "Bulk CSV import: ${filename}"`}
        >
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={importing}
              onClick={() => void runImport()}
              className="inline-flex items-center gap-1.5 px-3 py-2 text-caption font-medium btn-primary disabled:opacity-60"
            >
              {importing ? (
                <Loader2 size={13} className="animate-spin" aria-hidden />
              ) : (
                <FileUp size={13} aria-hidden />
              )}
              {importing
                ? `Imported ${imported} of ${rows.length}…`
                : `Import ${rows.length} row${rows.length === 1 ? '' : 's'}`}
            </button>
            {!importing && (
              <button type="button" onClick={reset} className={smallBtn}>
                Cancel
              </button>
            )}
          </div>
          {failures.length > 0 && (
            <ul className="mt-3 space-y-1">
              {failures.map((failure) => (
                <li
                  key={failure.line}
                  className="text-caption text-critical leading-relaxed"
                >
                  Line {failure.line}: {failure.message}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      )}

      {done && (
        <SectionCard
          title={`Imported ${imported} of ${rows.length} rows from ${filename}`}
          subtitle={
            failures.length === 0
              ? 'All rows written through the audited register endpoints.'
              : `${failures.length} row${failures.length === 1 ? '' : 's'} failed — fix and re-import just those rows.`
          }
          actions={
            <button type="button" onClick={reset} className={smallBtn}>
              <X size={13} aria-hidden />
              Close
            </button>
          }
        >
          {failures.length === 0 ? (
            <p className="text-caption text-success font-medium">
              Register refreshed below.
            </p>
          ) : (
            <ul className="space-y-1">
              {failures.map((failure) => (
                <li
                  key={failure.line}
                  className="text-caption text-critical leading-relaxed"
                >
                  Line {failure.line}: {failure.message}
                </li>
              ))}
            </ul>
          )}
        </SectionCard>
      )}
    </div>
  );
}
