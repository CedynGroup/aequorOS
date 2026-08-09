'use client';

import { useState, type ReactNode } from 'react';
import { AlertTriangle, Check, Copy, Inbox, RotateCw } from 'lucide-react';
import {
  ApiError,
  type TenantFreshnessSummary,
  type TenantIngestionSummary,
} from '@/lib/api';
import { DASH, fmtDate, fmtTs } from '@/lib/format';

// --------------------------------------------------------------------------
// Chips
// --------------------------------------------------------------------------

export type Tone = 'ok' | 'warn' | 'crit' | 'accent' | 'neutral';

const TONE_CLASSES: Record<Tone, string> = {
  ok: 'bg-success-light text-success',
  warn: 'bg-warning-light text-warning',
  crit: 'bg-critical-light text-critical',
  accent: 'bg-action-light text-action',
  neutral: 'bg-surface text-slate border border-border-light',
};

export function Chip({
  tone = 'neutral',
  mono = false,
  title,
  children,
}: {
  tone?: Tone;
  mono?: boolean;
  title?: string;
  children: ReactNode;
}) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-micro font-medium uppercase tracking-wide whitespace-nowrap ${
        mono ? 'font-mono normal-case tracking-normal' : ''
      } ${TONE_CLASSES[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * Keyword → tone inference for API status vocabularies the console does not
 * control (freshness summaries, batch outcomes, connection + activity
 * statuses). Unknown vocabulary renders neutral — never guess a severity.
 */
export function toneFor(value: string | null | undefined): Tone {
  if (!value) return 'neutral';
  const v = value.toLowerCase();
  if (/(fail|error|rolled_back|revoked|critical|unhealthy|down)/.test(v)) return 'crit';
  if (/(stale|partial|degraded|pending|warn|skipped|expiring|overdue)/.test(v)) return 'warn';
  if (/(succe|complete|healthy|fresh|active|connected|enabled|ok|current|live)/.test(v)) return 'ok';
  return 'neutral';
}

/** Chip that renders an API-provided status string with inferred tone, or a dash. */
export function StatusChip({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-slate-light">{DASH}</span>;
  return <Chip tone={toneFor(value)}>{value.replace(/_/g, ' ')}</Chip>;
}

/**
 * Live-metrics freshness, derived strictly from the API's summary object:
 * stale module count when stale, reported-module count when fresh, and a
 * plain "no modules" when nothing has computed yet.
 */
export function FreshnessChip({ summary }: { summary: TenantFreshnessSummary | null }) {
  if (!summary) return <span className="text-slate-light">{DASH}</span>;
  const title = [
    summary.stale_modules.length > 0 ? `stale: ${summary.stale_modules.join(', ')}` : null,
    summary.latest_computed_at ? `latest computed ${fmtTs(summary.latest_computed_at)}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  if (summary.is_stale) {
    return (
      <Chip tone="warn" title={title || undefined}>
        stale · {summary.stale_modules.length || '?'}
      </Chip>
    );
  }
  if (summary.modules_reported === 0) {
    return <Chip title={title || undefined}>no modules</Chip>;
  }
  return (
    <Chip tone="ok" title={title || undefined}>
      fresh · {summary.modules_reported} mod
    </Chip>
  );
}

/** Last ingestion batch outcome — status chip with the batch evidence in the tooltip. */
export function IngestionChip({ summary }: { summary: TenantIngestionSummary | null }) {
  if (!summary) return <span className="text-slate-light">{DASH}</span>;
  const title = `batch ${summary.batch_id} · ${summary.source_system} · as of ${fmtDate(
    summary.as_of_date,
  )}${summary.completed_at ? ` · completed ${fmtTs(summary.completed_at)}` : ''}`;
  return (
    <Chip tone={toneFor(summary.status)} title={title}>
      {summary.status.replace(/_/g, ' ')}
    </Chip>
  );
}

/** SSO lifecycle: enabled ▸ configured-but-disabled ▸ absent. */
export function SsoChip({ configured, enabled }: { configured: boolean; enabled: boolean }) {
  if (enabled) return <Chip tone="accent">SSO enabled</Chip>;
  if (configured) return <Chip tone="warn">SSO configured</Chip>;
  return <Chip>no SSO</Chip>;
}

// --------------------------------------------------------------------------
// Identity (mono id + copy)
// --------------------------------------------------------------------------

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={label ?? `Copy ${value}`}
      title={label ?? 'Copy'}
      onClick={() => {
        void navigator.clipboard.writeText(value).then(() => {
          setCopied(true);
          window.setTimeout(() => setCopied(false), 1500);
        });
      }}
      className="inline-flex items-center rounded p-1 text-slate hover:bg-surface hover:text-ink"
    >
      {copied ? <Check size={13} className="text-success" /> : <Copy size={13} />}
    </button>
  );
}

/** Platform id (BK-/OR-) in mono with an inline copy button. */
export function MonoId({ id, className = '' }: { id: string; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-0.5 ${className}`}>
      <span className="font-mono text-caption text-ink">{id}</span>
      <CopyButton value={id} />
    </span>
  );
}

// --------------------------------------------------------------------------
// Async states
// --------------------------------------------------------------------------

export function Skeleton({ className = '' }: { className?: string }) {
  return <div className={`skeleton ${className}`} aria-hidden="true" />;
}

/** Table-shaped loading state. */
export function SkeletonRows({ rows = 5, className = '' }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 p-4 ${className}`} role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-9 w-full" />
      ))}
    </div>
  );
}

export function ErrorPanel({
  error,
  onRetry,
  context,
}: {
  error: ApiError;
  onRetry?: () => void;
  context?: string;
}) {
  const unauthorized = error.status === 401;
  return (
    <div className="card border-critical/40 p-4" role="alert">
      <div className="flex items-start gap-3">
        <AlertTriangle size={18} className="mt-0.5 shrink-0 text-critical" />
        <div className="min-w-0 flex-1">
          <p className="text-body font-medium text-navy">
            {context ? `${context} failed` : 'Request failed'}
          </p>
          <p className="mt-1 break-words text-body text-slate">
            <span className="font-mono text-caption text-critical">{error.code}</span>
            {' · '}
            {error.message}
          </p>
          <div className="mt-3 flex items-center gap-2">
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="btn-primary inline-flex items-center gap-1.5 px-3 py-1.5 text-caption font-medium"
              >
                <RotateCw size={13} /> Retry
              </button>
            )}
            {unauthorized && (
              <a
                href="/login"
                className="inline-flex items-center rounded border border-border px-3 py-1.5 text-caption font-medium text-ink hover:bg-surface"
              >
                Sign in again
              </a>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function EmptyState({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 p-10 text-center">
      <Inbox size={22} className="text-slate-light" />
      <p className="text-body font-medium text-navy">{title}</p>
      {hint && <p className="max-w-md text-caption text-slate">{hint}</p>}
    </div>
  );
}

// --------------------------------------------------------------------------
// Layout helpers
// --------------------------------------------------------------------------

export function PageHeader({ title, sub }: { title: string; sub?: ReactNode }) {
  return (
    <div className="mb-5">
      <h1 className="text-h1 text-navy">{title}</h1>
      {sub && <p className="mt-1 text-body text-slate">{sub}</p>}
    </div>
  );
}

export function FieldRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-caption uppercase tracking-wide text-slate">{label}</span>
      <span className="min-w-0 text-right text-body text-ink">{children}</span>
    </div>
  );
}
