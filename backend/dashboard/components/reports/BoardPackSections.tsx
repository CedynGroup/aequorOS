'use client';

/**
 * Building blocks for the print-optimized board pack: an A4 "page" section
 * wrapper, a print-friendly metric table (tables beat charts on paper), and
 * the per-module brief block with status, validation counts, and run
 * provenance.
 */

import type { ReactNode } from 'react';
import StatusPill, { type StatusTone } from '@/components/ui/StatusPill';
import RunBadge, { type RunBadgeRun } from '@/components/ui/RunBadge';
import { fmtTimestamp } from '@/lib/api/values';

/** One printable A4 section — print.css inserts a page break after each. */
export function BoardPage({
  children,
  className = '',
}: {
  children: ReactNode;
  className?: string;
}) {
  return <section className={`bp-page ${className}`}>{children}</section>;
}

export type MetricRow = {
  label: string;
  value: string;
  /** Optional status pill next to the value. */
  tone?: StatusTone;
  toneLabel?: string;
  /** Secondary note (threshold, basis). */
  hint?: string;
};

/** Dense two-column metric table — the board pack's print-fidelity core. */
export function MetricTable({ rows }: { rows: MetricRow[] }) {
  return (
    <table className="w-full text-body border-collapse tnum">
      <thead className="sr-only">
        <tr>
          <th scope="col">Metric</th>
          <th scope="col">Value</th>
          <th scope="col">Status</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.label} className="border-b border-border-light last:border-b-0">
            <td className="py-2.5 pr-4 text-navy/85">
              {row.label}
              {row.hint && (
                <span className="block text-caption text-slate">{row.hint}</span>
              )}
            </td>
            <td className="py-2.5 pr-4 text-right font-mono text-navy num whitespace-nowrap">
              {row.value}
            </td>
            <td className="py-2.5 w-32 text-right">
              {row.tone ? (
                <StatusPill tone={row.tone}>{row.toneLabel}</StatusPill>
              ) : (
                <span aria-hidden />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/**
 * One module brief: header (name, status, validation counts), metric table,
 * and a provenance footer (official-run badge when one exists, otherwise the
 * live engine's computed-at timestamp).
 */
export function ModuleBrief({
  name,
  code,
  statusTone,
  statusLabel,
  validations,
  rows,
  run,
  computedAt,
  children,
}: {
  name: string;
  /** Module code chip, e.g. "02". */
  code?: string;
  statusTone: StatusTone;
  statusLabel: string;
  /** Validation totals when the dashboard payload carries them. */
  validations?: { total: number; failed: number };
  rows: MetricRow[];
  /** Latest successful official run for the module, when one exists. */
  run?: RunBadgeRun;
  /** Live-engine computed-at fallback when no official run exists yet. */
  computedAt?: Date;
  children?: ReactNode;
}) {
  return (
    <div className="card overflow-hidden bp-avoid-break">
      <div className="flex items-start justify-between gap-4 px-5 py-4 border-b border-border-light">
        <div className="min-w-0">
          <h3 className="text-h3 text-navy inline-flex items-center gap-2">
            {name}
            {code && (
              <span className="font-mono text-[10px] text-slate tnum">
                {code}
              </span>
            )}
          </h3>
          {validations && (
            <p className="mt-0.5 text-caption text-slate">
              {validations.total} validation
              {validations.total === 1 ? '' : 's'} ·{' '}
              {validations.failed === 0 ? (
                <span className="text-success">all passed</span>
              ) : (
                <span className="text-critical">
                  {validations.failed} failed
                </span>
              )}
            </p>
          )}
        </div>
        <StatusPill tone={statusTone} className="shrink-0">
          {statusLabel}
        </StatusPill>
      </div>

      <div className="px-5 py-2">
        <MetricTable rows={rows} />
      </div>
      {children}

      <div className="flex items-center justify-between gap-3 flex-wrap px-5 py-2.5 border-t border-border-light bg-surface/60">
        <span className="text-caption text-slate">
          {run
            ? 'Official run provenance'
            : computedAt
            ? `Live figures · computed ${fmtTimestamp(computedAt)}`
            : 'No official run minted for this period yet'}
        </span>
        {run && <RunBadge run={run} />}
      </div>
    </div>
  );
}
