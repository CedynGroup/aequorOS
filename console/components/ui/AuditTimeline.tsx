import type { ReactNode } from 'react';
import { DASH, fmtTs, relTime } from '@/lib/format';
import { toneFor, type Tone } from './Chip';

export type AuditEvent = {
  id?: string;
  /** Who acted (operator email / actor id). */
  actor?: ReactNode;
  /** What happened — the action verb/label. */
  action: ReactNode;
  /** ISO timestamp. */
  at?: string | null;
  /** Free-form detail line. */
  detail?: ReactNode;
  /** Status string — tone is inferred, or override with `tone`. */
  status?: string | null;
  tone?: Tone;
};

const DOT_TONE: Record<Tone, string> = {
  ok: 'bg-success',
  warn: 'bg-warning',
  crit: 'bg-critical',
  accent: 'bg-action',
  neutral: 'bg-slate',
};

/**
 * Vertical who / when / what / status timeline for audit and lifecycle feeds
 * (operator audit log, determination history). Newest-first is the caller's
 * choice — this renders the list order as given.
 */
export function AuditTimeline({
  events,
  className = '',
}: {
  events: AuditEvent[];
  className?: string;
}) {
  if (events.length === 0) {
    return <p className={`text-caption text-slate ${className}`}>No activity recorded.</p>;
  }
  return (
    <ol className={className}>
      {events.map((e, i) => {
        const tone: Tone = e.tone ?? (e.status ? toneFor(e.status) : 'neutral');
        const isLast = i === events.length - 1;
        return (
          <li key={e.id ?? i} className="relative flex gap-3 pb-5 last:pb-0">
            {!isLast && (
              <span
                aria-hidden
                className="absolute left-[5px] top-4 h-[calc(100%-0.5rem)] w-px bg-border-light"
              />
            )}
            <span
              aria-hidden
              className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ring-2 ring-[color:rgb(var(--surface-raised))] ${DOT_TONE[tone]}`}
            />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                <p className="min-w-0 text-body text-navy">
                  {e.actor && <span className="font-medium">{e.actor}</span>}
                  {e.actor && ' '}
                  <span className={e.actor ? 'text-navy/85' : 'font-medium'}>{e.action}</span>
                </p>
                {e.at && (
                  <span className="shrink-0 text-caption text-slate" title={fmtTs(e.at)}>
                    {relTime(e.at)}
                  </span>
                )}
              </div>
              {e.detail && <p className="mt-0.5 break-words text-caption text-slate">{e.detail}</p>}
              {e.status && (
                <span className="mt-1 inline-block text-micro font-medium uppercase tracking-wide text-slate">
                  {e.status.replace(/_/g, ' ')}
                </span>
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** Empty-safe helper for callers that only have a single timestamp string. */
export function auditRelTime(iso: string | null | undefined): string {
  return iso ? relTime(iso) : DASH;
}
