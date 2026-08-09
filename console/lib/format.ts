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
