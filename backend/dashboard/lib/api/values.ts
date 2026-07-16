/**
 * Display helpers for API payloads. The generated client surfaces backend
 * Decimals as strings; these convert them for chart/format consumption and
 * map backend statuses onto the demo's visual tones. Display-only — no
 * regulatory math happens client-side.
 */

import type { StatusTone } from '@/components/ui/StatusPill';

/** Parse a backend decimal string (or number) for display. */
export function num(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/** Backend traffic-light status → demo StatusPill tone. */
export function statusTone(
  status: 'green' | 'amber' | 'red' | string | null | undefined
): StatusTone {
  switch (status) {
    case 'green':
      return 'compliant';
    case 'amber':
      return 'approaching';
    case 'red':
      return 'breach';
    default:
      return 'pending';
  }
}

/** Validation severity → StatusPill tone (for failed rules). */
export function severityTone(
  severity: 'error' | 'warning' | 'info' | string
): StatusTone {
  switch (severity) {
    case 'error':
      return 'critical';
    case 'warning':
      return 'amber';
    default:
      return 'slate';
  }
}

/** "9b1960dedc91…" — shorten a hash/uuid for run badges. */
export function shortId(value: string, length = 8): string {
  return value.length > length ? `${value.slice(0, length)}` : value;
}

/** "snake_case_label" → "Snake Case Label". */
export function labelize(value: string): string {
  return value
    .split(/[_\s]+/)
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

/** Format a run timestamp: "14 Jul 2026 20:58". */
export function fmtTimestamp(d: Date): string {
  return `${d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  })} ${d.toLocaleTimeString('en-GB', {
    hour: '2-digit',
    minute: '2-digit',
  })}`;
}

/**
 * Compact relative time for live/freshness signals: "just now", "3m ago",
 * "2h ago", "5d ago". Falls back to an absolute timestamp beyond a week so a
 * stale figure never masquerades as recent. Accepts a Date or ISO string;
 * returns "—" for missing input.
 */
export function fmtRelative(
  value: Date | string | null | undefined
): string {
  if (value === null || value === undefined) return '—';
  const then = typeof value === 'string' ? new Date(value) : value;
  const ms = then.getTime();
  if (!Number.isFinite(ms)) return '—';
  const diff = Date.now() - ms;
  if (diff < 0) return 'just now';
  const sec = Math.floor(diff / 1000);
  if (sec < 45) return 'just now';
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 7) return `${day}d ago`;
  return fmtTimestamp(then);
}

/** ISO date-only (YYYY-MM-DD) in UTC — the form pipeline as-of dates take. */
export function isoDate(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/**
 * Format a date-only value (e.g. reporting period end) in UTC so that
 * "2026-03-31" renders as 31 Mar 2026 in every timezone.
 */
export function fmtDateUTC(d: Date): string {
  return d.toLocaleDateString('en-GB', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
    timeZone: 'UTC',
  });
}
