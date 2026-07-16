'use client';

/**
 * Shared Data Engine UI atoms: batch status pills, artifact path chips, and
 * the validation report summary strip.
 */

import type { IngestionBatchRead } from '@aequoros/risk-service-api';
import { BATCH_STATUS_EXPLAINERS } from './content';

const STATUS_STYLES: Record<string, string> = {
  accepted: 'bg-success-light text-success border-success/30',
  accepted_with_warnings: 'bg-warning-light text-warning border-warning/30',
  rejected: 'bg-critical-light text-critical border-critical/30',
  failed: 'bg-critical-light text-critical border-critical/30',
};

export function BatchStatusPill({ status }: { status: string }) {
  const style =
    STATUS_STYLES[status] ?? 'bg-surface text-slate border-border';
  return (
    <span
      title={BATCH_STATUS_EXPLAINERS[status]}
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-caption font-medium whitespace-nowrap ${style}`}
    >
      {status.replaceAll('_', ' ')}
    </span>
  );
}

export type TableBreakdownEntry = {
  source_table: string;
  resolved_to: string | null;
  rows_extracted: number;
  rows_accepted: number;
  rows_warning: number;
  rows_error: number;
  rows_blocked: number;
  suggestion: string | null;
};

type ValidationReport = {
  summary?: {
    reference_rows?: Record<string, number>;
    [key: string]: unknown;
  };
  tables?: TableBreakdownEntry[];
  failures?: {
    rule: string;
    severity: string;
    entity_type: string | null;
    source_reference: string | null;
    source_locator: string | null;
    detail: string;
  }[];
  suppressed_findings?: Record<string, number>;
  [key: string]: unknown;
};

export function validationReport(batch: IngestionBatchRead): ValidationReport {
  return (batch.validationReport ?? {}) as ValidationReport;
}

export function referenceRowCounts(batch: IngestionBatchRead): Record<string, number> {
  return validationReport(batch).summary?.reference_rows ?? {};
}

export function referenceRowTotal(batch: IngestionBatchRead): number {
  return Object.values(referenceRowCounts(batch)).reduce((sum, count) => sum + count, 0);
}

/** Per-table extraction breakdown: every table found in the source. */
export function tablesBreakdown(batch: IngestionBatchRead): TableBreakdownEntry[] {
  return validationReport(batch).tables ?? [];
}

/**
 * Dense per-table view: tab/file-table name → what it resolved to → row
 * outcomes. Unmatched tables render in warning tone with the near-miss
 * suggestion, so a multi-tab workbook never *looks* like one tab ingested.
 */
export function TablesBreakdownTable({ tables }: { tables: TableBreakdownEntry[] }) {
  if (tables.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-caption">
        <thead>
          <tr className="text-left text-micro uppercase tracking-wider text-slate">
            <th className="py-1.5 pr-4 font-medium">Table in source</th>
            <th className="py-1.5 pr-4 font-medium">Resolved to</th>
            <th className="py-1.5 pr-4 font-medium text-right">Extracted</th>
            <th className="py-1.5 pr-4 font-medium text-right">Accepted</th>
            <th className="py-1.5 pr-4 font-medium text-right">Warnings</th>
            <th className="py-1.5 font-medium text-right">Errors</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-border-light">
          {tables.map((entry) => {
            const unmatched = entry.resolved_to === null;
            return (
              <tr key={entry.source_table}>
                <td className="py-1.5 pr-4 font-mono text-navy">
                  {entry.source_table}
                  {unmatched && entry.suggestion && (
                    <p className="mt-0.5 font-sans text-micro text-warning">
                      {entry.suggestion}
                    </p>
                  )}
                </td>
                <td
                  className={`py-1.5 pr-4 font-mono ${unmatched ? 'text-warning' : 'text-navy'}`}
                >
                  {entry.resolved_to ?? 'not mapped — skipped'}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono text-navy">
                  {entry.rows_extracted}
                </td>
                <td className="py-1.5 pr-4 text-right font-mono text-success">
                  {entry.rows_accepted}
                </td>
                <td
                  className={`py-1.5 pr-4 text-right font-mono ${entry.rows_warning > 0 ? 'text-warning' : 'text-slate'}`}
                >
                  {entry.rows_warning}
                </td>
                <td
                  className={`py-1.5 text-right font-mono ${entry.rows_error + entry.rows_blocked > 0 ? 'text-critical' : 'text-slate'}`}
                >
                  {entry.rows_error + entry.rows_blocked}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Compact one-line chips per table for upload result rows. */
export function TablesChips({ batch }: { batch: IngestionBatchRead }) {
  const tables = tablesBreakdown(batch);
  if (tables.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {tables.map((entry) => {
        const unmatched = entry.resolved_to === null;
        return (
          <span
            key={entry.source_table}
            title={unmatched ? (entry.suggestion ?? 'No mapping matched this table.') : undefined}
            className={`inline-flex items-center gap-1 rounded border px-2 py-0.5 text-micro font-mono ${
              unmatched
                ? 'border-warning/40 bg-warning-light/40 text-warning'
                : 'border-border text-navy'
            }`}
          >
            {entry.source_table}
            <span className={unmatched ? '' : 'text-slate'}>
              {unmatched
                ? '→ unmatched'
                : `→ ${entry.resolved_to} (${entry.rows_extracted})`}
            </span>
          </span>
        );
      })}
    </div>
  );
}

/** BLOCKER finding details — the found-versus-expected diagnosis on rejection. */
export function batchBlockerDetails(batch: IngestionBatchRead): string[] {
  return (validationReport(batch).failures ?? [])
    .filter((failure) => failure.severity === 'BLOCKER')
    .map((failure) => failure.detail);
}

export function CountStrip({ batch }: { batch: IngestionBatchRead }) {
  const referenceRows = referenceRowTotal(batch);
  const items: { label: string; value: number; tone?: string; title?: string }[] = [
    { label: 'Extracted', value: batch.recordsExtracted },
    { label: 'Translated', value: batch.recordsTranslated },
    {
      label: 'Accepted',
      value: batch.recordsAccepted,
      tone: 'text-success',
      title: 'Clean rows persisted to the canonical model.',
    },
    {
      label: 'Warnings',
      value: batch.recordsWarning,
      tone: 'text-warning',
      title:
        'Persisted rows flagged for data quality — they participate in calculations. Not rejections.',
    },
    {
      label: 'Errors',
      value: batch.recordsError,
      tone: 'text-critical',
      title: 'Rejected rows — excluded from the canonical model and calculations.',
    },
    {
      label: 'Blocked',
      value: batch.recordsBlocked,
      tone: 'text-critical',
      title: 'Rows blocked by a batch-level blocking failure.',
    },
    { label: 'Ref rows', value: referenceRows },
  ];
  return (
    <div className="grid grid-cols-4 sm:grid-cols-7 gap-px bg-border-light rounded overflow-hidden border border-border-light">
      {items.map((item) => (
        <div key={item.label} className="bg-surface-raised px-3 py-2" title={item.title}>
          <p className="text-micro uppercase tracking-wider text-slate">{item.label}</p>
          <p className={`mt-0.5 font-mono text-h3 ${item.tone ?? 'text-navy'}`}>
            {item.value}
          </p>
        </div>
      ))}
    </div>
  );
}

export function ArtifactPath({ label, path }: { label: string; path: string | null }) {
  if (!path) return null;
  return (
    <div className="flex items-baseline gap-2 min-w-0">
      <span className="text-caption text-slate shrink-0">{label}</span>
      <code className="text-caption font-mono text-navy truncate" title={path}>
        {path}
      </code>
    </div>
  );
}

export function formatDate(value: Date | string | null | undefined): string {
  if (!value) return '—';
  const parsed = typeof value === 'string' ? new Date(value) : value;
  return parsed.toISOString().slice(0, 10);
}

export function formatDateTime(value: Date | string | null | undefined): string {
  if (!value) return '—';
  const parsed = typeof value === 'string' ? new Date(value) : value;
  return parsed.toISOString().replace('T', ' ').slice(0, 19);
}
