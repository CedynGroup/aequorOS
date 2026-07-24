'use client';

/**
 * Renders the two reports a sync batch carries: the ETL preprocess report
 * (preprocess operations / flags + dedup linkage counts) and the validation
 * report (summary + findings). Both are untyped JSON on the batch, so rendering
 * is defensive: known shapes get first-class treatment, everything else falls
 * back to a generic key/value view.
 */

import type { IngestionBatchRead } from '@aequoros/risk-service-api';
import { fmtLocale } from '@/lib/format';

type ReportFinding = {
  rule?: string;
  severity?: string;
  entity_type?: string | null;
  source_reference?: string | null;
  source_locator?: string | null;
  detail?: string;
};

type ValidationReportShape = {
  status?: string;
  summary?: Record<string, unknown>;
  failures?: ReportFinding[];
  suppressed_findings?: Record<string, number>;
  etl_report?: Record<string, unknown>;
  etl?: Record<string, unknown>;
  preprocess?: Record<string, unknown>;
  [key: string]: unknown;
};

const SEVERITY_TONE: Record<string, string> = {
  BLOCKER: 'text-critical',
  ERROR: 'text-critical',
  WARNING: 'text-warning',
  INFO: 'text-slate',
};

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function humanize(key: string): string {
  return key
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function scalarText(value: unknown): string {
  if (value == null) return '—';
  if (typeof value === 'number') return value.toLocaleString(fmtLocale());
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

/** Compact stat chip for a scalar entry (count, flag, boolean). */
function StatChip({ label, value }: { label: string; value: unknown }) {
  return (
    <span className="inline-flex items-center gap-2 rounded border border-border px-2.5 py-1 text-caption font-mono text-navy">
      {humanize(label)}
      <span className="text-slate tabular-nums">{scalarText(value)}</span>
    </span>
  );
}

/** Render one section of an untyped report object: scalars become stat chips,
 * string arrays become a chip list, object maps become nested stat chips, and
 * arrays of objects collapse to a count. */
function ReportSection({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data);
  if (entries.length === 0) {
    return <p className="text-caption text-slate">No detail reported.</p>;
  }
  return (
    <div className="space-y-3">
      {entries.map(([key, value]) => {
        if (Array.isArray(value)) {
          if (value.length === 0) {
            return <StatChip key={key} label={key} value={0} />;
          }
          if (value.every((item) => typeof item === 'string' || typeof item === 'number')) {
            return (
              <div key={key}>
                <p className="text-caption font-medium text-slate mb-1">{humanize(key)}</p>
                <div className="flex flex-wrap gap-1.5">
                  {value.map((item, index) => (
                    <span
                      key={index}
                      className="rounded bg-surface border border-border-light px-2 py-0.5 text-caption font-mono text-navy"
                    >
                      {scalarText(item)}
                    </span>
                  ))}
                </div>
              </div>
            );
          }
          return <StatChip key={key} label={`${key} (count)`} value={value.length} />;
        }
        if (isPlainObject(value)) {
          return (
            <div key={key}>
              <p className="text-caption font-medium text-slate mb-1">{humanize(key)}</p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(value).map(([nestedKey, nestedValue]) => (
                  <StatChip
                    key={nestedKey}
                    label={nestedKey}
                    value={
                      isPlainObject(nestedValue) || Array.isArray(nestedValue)
                        ? scalarText(nestedValue)
                        : nestedValue
                    }
                  />
                ))}
              </div>
            </div>
          );
        }
        return <StatChip key={key} label={key} value={value} />;
      })}
    </div>
  );
}

export default function BatchReport({ batch }: { batch: IngestionBatchRead }) {
  const report = (batch.validationReport ?? {}) as ValidationReportShape;
  // etl_report is a first-class column on the batch (ML-ETL preprocess + dedup summary),
  // returned typed on IngestionBatchRead — not nested under the validation report.
  const etl = (batch.etlReport ?? null) as Record<string, unknown> | null;
  const findings = report.failures ?? [];
  const suppressed = report.suppressed_findings ?? {};

  return (
    <div className="space-y-4">
      <section className="rounded border border-border p-4 bg-surface space-y-2">
        <h4 className="text-body font-medium text-navy">ETL preprocess</h4>
        {etl && isPlainObject(etl) ? (
          <ReportSection data={etl} />
        ) : (
          <p className="text-caption text-slate">
            No ETL preprocess report was attached to this batch.
          </p>
        )}
      </section>

      <section className="rounded border border-border p-4 bg-surface space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h4 className="text-body font-medium text-navy">Validation report</h4>
          {report.status && (
            <span className="text-caption font-mono text-slate">{report.status}</span>
          )}
        </div>

        {report.summary && isPlainObject(report.summary) && (
          <ReportSection data={report.summary} />
        )}

        {Object.keys(suppressed).length > 0 && (
          <p className="text-caption text-slate">
            Large batch:{' '}
            {Object.entries(suppressed)
              .map(([rule, count]) => `${count} further ${rule} findings`)
              .join(', ')}{' '}
            counted in the totals but not listed.
          </p>
        )}

        {findings.length === 0 ? (
          <p className="text-caption text-slate">
            No validation findings — every record passed the configured rules.
          </p>
        ) : (
          <div className="divide-y divide-border-light border-t border-border-light">
            {findings.map((finding, index) => (
              <div key={index} className="py-2.5 flex items-start gap-3">
                <span
                  className={`shrink-0 w-16 text-caption font-medium ${
                    SEVERITY_TONE[finding.severity ?? ''] ?? 'text-slate'
                  }`}
                >
                  {finding.severity ?? 'INFO'}
                </span>
                <div className="min-w-0">
                  <p className="text-body text-navy">{finding.detail}</p>
                  <p className="mt-0.5 text-caption font-mono text-slate truncate">
                    {finding.rule}
                    {finding.source_reference ? ` · ${finding.source_reference}` : ''}
                    {finding.source_locator ? ` · ${finding.source_locator}` : ''}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
