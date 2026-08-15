/**
 * Display helpers. NO fabrication: anything missing or unparseable renders
 * as an em dash or the raw string the API sent.
 */

export const DASH = '—';

/** Relative time ("4m ago", "in 2h") from an ISO timestamp; DASH when absent. */
export function relTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const diffMs = t - Date.now();
  const past = diffMs <= 0;
  let s = Math.round(Math.abs(diffMs) / 1000);
  let label: string;
  if (s < 45) label = `${s}s`;
  else if ((s = Math.round(s / 60)) < 60) label = `${s}m`;
  else if ((s = Math.round(s / 60)) < 36) label = `${s}h`;
  else if ((s = Math.round(s / 24)) < 45) label = `${s}d`;
  else label = `${Math.round(s / 30.44)}mo`;
  return past ? `${label} ago` : `in ${label}`;
}

/** Date portion (YYYY-MM-DD) of an ISO string; DASH when absent. */
export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toISOString().slice(0, 10);
}

/** Full timestamp for title/tooltips; empty string when absent. */
export function fmtTs(iso: string | null | undefined): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  return new Date(t).toISOString().replace('T', ' ').replace(/\.\d+Z$/, 'Z');
}

/**
 * Compact timestamp for footer/meta rows — YYYY-MM-DD HH:MM (UTC); DASH when
 * absent. Accepts either an ISO string or a Date (the SectionCard/RunBadge
 * grammar ported from the dashboard hands in Dates).
 */
export function fmtTimestamp(input: string | Date | null | undefined): string {
  if (input === null || input === undefined) return DASH;
  const t = input instanceof Date ? input.getTime() : Date.parse(input);
  if (Number.isNaN(t)) return typeof input === 'string' ? input : DASH;
  return new Date(t).toISOString().replace('T', ' ').replace(/:\d{2}\.\d+Z$/, 'Z');
}

/** Truncate a long id/hash for display (keeps the leading `len` chars + ellipsis). */
export function shortId(value: string | null | undefined, len = 8): string {
  if (!value) return DASH;
  return value.length <= len ? value : `${value.slice(0, len)}…`;
}

/**
 * Coerce a decimal-as-string (the desk wire convention — values ride as JSON
 * strings to preserve precision) or a number to a JS number; 0 when unparseable.
 */
export function num(value: string | number | null | undefined): number {
  if (value === null || value === undefined || value === '') return 0;
  const n = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(n) ? n : 0;
}
