/**
 * Display helpers for API payloads. The generated client surfaces backend
 * Decimals as strings; these convert them for chart/format consumption and
 * map backend statuses onto the demo's visual tones. Display-only — no
 * regulatory math happens client-side.
 */

import type { StatusTone } from '@/components/ui/StatusPill';

/**
 * Parse a backend decimal string (or number) for display.
 *
 * ZERO ON ABSENCE. `null`, `undefined` and an unparseable string all collapse
 * to `0`. That is only safe for a figure the contract declares NON-nullable
 * (a count, a total the backend always emits, a share that is genuinely zero
 * when empty). For a NULLABLE regulatory figure use `numOrNull`: on screen a
 * fabricated `0` is indistinguishable from a measured zero, it plots as a real
 * data point, and — because it compares below every floor and above every
 * zeroed floor — it silently decides breach-vs-compliant. That was the
 * mechanism behind the stress workbench passing every breached CAR.
 */
export function num(value: string | number | null | undefined): number {
  if (value === null || value === undefined) return 0;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Null-preserving parse for a NULLABLE figure — the regulatory counterpart of
 * `num()`. Absence stays absence so the caller is forced to render it as
 * absence ("—", "not assessed", a gap in the line) rather than as a measured
 * zero. Use this for any value whose type is `… | null`, for any ratio a regime
 * may not compute, and for every regulatory floor.
 */
export function numOrNull(
  value: string | number | null | undefined
): number | null {
  if (value === null || value === undefined || value === '') return null;
  const parsed = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * The outcome of comparing a ratio with its regulatory floor.
 *
 * `assessed: false` is a first-class result, not an error: it is what the UI
 * must render whenever the comparison cannot honestly be made.
 */
export type FloorAssessment =
  | { assessed: true; breach: boolean; value: number; floor: number }
  | { assessed: false; reason: 'no_value' | 'no_floor' | 'neither' };

/**
 * Compare a ratio against a floor, FAILING CLOSED.
 *
 * A missing measurement or a missing floor yields `assessed: false` — never a
 * pass. A non-positive floor counts as missing: a 0% capital or liquidity floor
 * cannot discriminate (every value clears it), so it is the sentinel of an
 * unconfigured threshold, not a threshold. Callers must render an
 * `assessed: false` outcome as indeterminate — never green, never "compliant".
 */
export function assessAgainstFloor(
  value: number | null | undefined,
  floor: number | null | undefined
): FloorAssessment {
  const hasValue = typeof value === 'number' && Number.isFinite(value);
  const hasFloor =
    typeof floor === 'number' && Number.isFinite(floor) && floor > 0;
  if (hasValue && hasFloor) {
    return { assessed: true, breach: value < floor, value, floor };
  }
  if (!hasValue && !hasFloor) return { assessed: false, reason: 'neither' };
  return { assessed: false, reason: hasValue ? 'no_floor' : 'no_value' };
}

/**
 * KPI edge tone for a floor assessment. An unassessed comparison is `warn`
 * (attention required) — deliberately NEVER `ok`, because the green edge on a
 * KPI reads as a compliance affirmation.
 */
export function floorStatus(
  assessment: FloorAssessment
): 'ok' | 'warn' | 'crit' {
  if (!assessment.assessed) return 'warn';
  return assessment.breach ? 'crit' : 'ok';
}

/** Plain-language reason an assessment could not be made. */
export function floorNotAssessedReason(
  assessment: FloorAssessment,
  what: string
): string | null {
  if (assessment.assessed) return null;
  switch (assessment.reason) {
    case 'no_floor':
      return `No ${what} floor configured — compliance not assessed`;
    case 'no_value':
      return `${what} not computed — compliance not assessed`;
    default:
      return `Neither ${what} nor its floor is available — compliance not assessed`;
  }
}

/** A module's traffic light, where 'na' means "not computable". */
export type ModuleStatus = 'green' | 'amber' | 'red' | 'na';

/**
 * The verdict a compliance banner may draw over a set of module statuses.
 * `not_assessed` and `partial` exist precisely so that "nothing was measured"
 * and "some things were measured" cannot collapse into `compliant`.
 */
export type ComplianceVerdict =
  | 'breach'
  | 'partial'
  | 'compliant'
  | 'not_assessed';

/**
 * Summarise a set of in-scope module statuses, FAILING CLOSED.
 *
 *   any 'red'            → 'breach'
 *   nothing computable   → 'not_assessed'   (never a compliance affirmation)
 *   some 'na' remaining  → 'partial'        (compliance holds only over what was measured)
 *   otherwise            → 'compliant'
 *
 * Pass ONLY the modules that are in scope for the institution: a module the
 * tenant does not run is 'na' by design and must not be reported as unmeasured.
 */
export function moduleComplianceVerdict(
  statuses: readonly ModuleStatus[]
): ComplianceVerdict {
  if (statuses.some((status) => status === 'red')) return 'breach';
  const computable = statuses.filter((status) => status !== 'na');
  if (computable.length === 0) return 'not_assessed';
  if (computable.length < statuses.length) return 'partial';
  return 'compliant';
}

/**
 * "13%" / "12.50%" — a regulatory-floor caption. Never rounds a real threshold
 * away (an SDI s.29 floor of 12.5% must not read as "13%").
 */
export function fmtFloorPct(value: number): string {
  return `${value % 1 === 0 ? value.toFixed(0) : value.toFixed(2)}%`;
}

/** "12.40%" for a measured ratio, or a fallback for a missing one. */
export function fmtPctOrNull(
  value: number | null | undefined,
  decimals = 2,
  fallback = '—'
): string {
  return typeof value === 'number' && Number.isFinite(value)
    ? `${value.toFixed(decimals)}%`
    : fallback;
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
