/**
 * Shared helpers for the operator console's Tenants / operations surfaces.
 * Pure functions only — no fabrication: missing inputs return DASH or null so
 * callers render an em dash rather than a guessed value.
 */

import type { Tone } from '@/components/ui';
import type { ApiError } from '@/lib/api';
import { DASH, relTime } from '@/lib/format';

// --------------------------------------------------------------------------
// Inspector-session gating
// --------------------------------------------------------------------------

/**
 * True when a deep-read failed purely because the inspection session is gone
 * (the backend's 403 `inspection_required`, or any 403 from a session-gated
 * route). The caller renders the re-inspect placeholder rather than a raw error
 * panel — the expected state when a session expires mid-view.
 */
export function isInspectionRequired(error: ApiError | null | undefined): boolean {
  return Boolean(error && (error.code === 'inspection_required' || error.status === 403));
}

// --------------------------------------------------------------------------
// JsonObject rendering (metric outputs, mapping config, validation reports)
// --------------------------------------------------------------------------

/** Pretty-print any JSON value for a diagnostic viewer; DASH for null/empty. */
export function formatJson(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Compact one-line rendering of a single JSON scalar value (for table cells). */
export function formatScalar(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 4 });
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'string') return value;
  return JSON.stringify(value);
}

/**
 * Summarize the first few key→value pairs of a metric/output object as
 * `key=value` fragments (nested objects/arrays collapse to their type), so a
 * table cell surfaces the actual numbers without a fabricated headline.
 */
export function summarizeObject(obj: Record<string, unknown> | null | undefined, max = 4): string {
  if (!obj) return DASH;
  const entries = Object.entries(obj);
  if (entries.length === 0) return DASH;
  const shown = entries.slice(0, max).map(([k, v]) => {
    if (v !== null && typeof v === 'object') return `${k}=${Array.isArray(v) ? '[…]' : '{…}'}`;
    return `${k}=${formatScalar(v)}`;
  });
  if (entries.length > max) shown.push(`+${entries.length - max} more`);
  return shown.join(' · ');
}

// --------------------------------------------------------------------------
// Client-side CSV export
// --------------------------------------------------------------------------

type CsvValue = string | number | null | undefined;

function csvCell(value: CsvValue): string {
  const s = value === null || value === undefined ? '' : String(value);
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/**
 * Build a CSV string from a header row + body rows. Values are escaped per
 * RFC-4180 (quote-wrap anything containing a comma, quote, or newline).
 */
export function toCsv(headers: string[], rows: CsvValue[][]): string {
  const lines = [headers.map(csvCell).join(',')];
  for (const row of rows) lines.push(row.map(csvCell).join(','));
  return lines.join('\r\n');
}

/**
 * Trigger a browser download of a CSV. Called only from event handlers in
 * client components, so `document`/`Blob` are always available.
 */
export function downloadCsv(filename: string, headers: string[], rows: CsvValue[][]): void {
  const blob = new Blob([toCsv(headers, rows)], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

/** `tenants-YYYY-MM-DD.csv` — a stable, sortable export filename. */
export function csvFilename(prefix: string): string {
  return `${prefix}-${new Date().toISOString().slice(0, 10)}.csv`;
}

// --------------------------------------------------------------------------
// Byte formatting (storage footprint)
// --------------------------------------------------------------------------

const BYTE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];

/** Human byte size (1024-based). DASH for null/undefined/non-finite — never guesses. */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return DASH;
  if (bytes <= 0) return '0 B';
  const idx = Math.min(BYTE_UNITS.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)));
  const val = bytes / 1024 ** idx;
  return `${val.toFixed(val >= 10 || idx === 0 ? 0 : 1)} ${BYTE_UNITS[idx]}`;
}

// --------------------------------------------------------------------------
// Credential-expiry health (data-engine connections)
// --------------------------------------------------------------------------

export interface CredentialHealth {
  tone: Tone;
  label: string;
  /** True once the credential is past its expiry. */
  expired: boolean;
  /** Whole days until expiry (negative once expired). */
  days: number;
}

/**
 * Classify a connection's credential expiry relative to now. Returns null when
 * the connection carries no expiry (a plain bearer / long-lived credential) so
 * the caller renders a dash rather than a false "healthy" claim.
 *   expired          → crit
 *   ≤ 14 days out    → crit  (act now)
 *   ≤ 30 days out    → warn
 *   further out      → neutral (informational)
 */
export function credentialHealth(iso: string | null | undefined): CredentialHealth | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const days = Math.floor((t - Date.now()) / 86_400_000);
  if (days < 0) return { tone: 'crit', label: `expired ${relTime(iso)}`, expired: true, days };
  if (days <= 14) return { tone: 'crit', label: `expires ${relTime(iso)}`, expired: false, days };
  if (days <= 30) return { tone: 'warn', label: `expires ${relTime(iso)}`, expired: false, days };
  return { tone: 'neutral', label: `expires ${relTime(iso)}`, expired: false, days };
}
