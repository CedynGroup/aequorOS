'use client';

/**
 * Shared Data Engine UI atoms: batch status pills, artifact path chips, and
 * the validation report summary strip.
 */

import type { IngestionBatchRead } from '@aequoros/risk-service-api';

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
      className={`inline-flex items-center px-2 py-0.5 rounded-full border text-caption font-medium whitespace-nowrap ${style}`}
    >
      {status.replaceAll('_', ' ')}
    </span>
  );
}

type ValidationReport = {
  summary?: {
    reference_rows?: Record<string, number>;
    [key: string]: unknown;
  };
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

/** BLOCKER finding details — the found-versus-expected diagnosis on rejection. */
export function batchBlockerDetails(batch: IngestionBatchRead): string[] {
  return (validationReport(batch).failures ?? [])
    .filter((failure) => failure.severity === 'BLOCKER')
    .map((failure) => failure.detail);
}

export function CountStrip({ batch }: { batch: IngestionBatchRead }) {
  const referenceRows = referenceRowTotal(batch);
  const items: { label: string; value: number; tone?: string }[] = [
    { label: 'Extracted', value: batch.recordsExtracted },
    { label: 'Translated', value: batch.recordsTranslated },
    { label: 'Accepted', value: batch.recordsAccepted, tone: 'text-success' },
    { label: 'Warnings', value: batch.recordsWarning, tone: 'text-warning' },
    { label: 'Errors', value: batch.recordsError, tone: 'text-critical' },
    { label: 'Blocked', value: batch.recordsBlocked, tone: 'text-critical' },
    { label: 'Ref rows', value: referenceRows },
  ];
  return (
    <div className="grid grid-cols-4 sm:grid-cols-7 gap-px bg-border-light rounded overflow-hidden border border-border-light">
      {items.map((item) => (
        <div key={item.label} className="bg-white px-3 py-2">
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
