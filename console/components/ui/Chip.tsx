import type { ReactNode } from 'react';
import type {
  TenantFreshnessSummary,
  TenantIngestionSummary,
} from '@/lib/api';
import { DASH, fmtDate, fmtTs } from '@/lib/format';

// --------------------------------------------------------------------------
// Chips — the console's status-vocabulary primitives (kept from the original
// ui.tsx; moved here verbatim as part of the design-system split).
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
