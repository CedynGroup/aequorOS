/**
 * Display formatting. Every time is shown in GMT (UTC), as the design labels
 * it. Month names are written out here rather than taken from Intl, whose
 * en-GB short month for September varies between ICU versions.
 */

const MONTHS_SHORT = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
const MONTHS_LONG = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;
const WEEKDAYS_SHORT = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/** Parses an RFC 3339 timestamp or a `YYYY-MM-DD` date (as UTC midnight). */
export function parseInstant(value: string | Date): Date | null {
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  const text = DATE_ONLY.test(value) ? `${value}T00:00:00Z` : value;
  const date = new Date(text);
  return Number.isNaN(date.getTime()) ? null : date;
}

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

function parts(value: string | Date) {
  const d = parseInstant(value);
  if (!d) return null;
  return {
    year: d.getUTCFullYear(),
    month: d.getUTCMonth(),
    day: d.getUTCDate(),
    weekday: d.getUTCDay(),
    hh: pad(d.getUTCHours()),
    mm: pad(d.getUTCMinutes()),
    ss: pad(d.getUTCSeconds()),
  };
}

const DASH = "—";

/** "31 Oct 2026" */
export function formatDate(value: string | Date | null | undefined): string {
  const p = value ? parts(value) : null;
  return p ? `${p.day} ${MONTHS_SHORT[p.month]} ${p.year}` : DASH;
}

/** "24 Nov" */
export function formatDayMonth(value: string | Date | null | undefined): string {
  const p = value ? parts(value) : null;
  return p ? `${p.day} ${MONTHS_SHORT[p.month]}` : DASH;
}

/** "Mon 23 Nov" */
export function formatWeekdayDate(value: string | Date | null | undefined): string {
  const p = value ? parts(value) : null;
  return p ? `${WEEKDAYS_SHORT[p.weekday]} ${p.day} ${MONTHS_SHORT[p.month]}` : DASH;
}

/** "16:38", or "16:38:04" with seconds. */
export function formatTime(value: string | Date | null | undefined, options: { seconds?: boolean } = {}): string {
  const p = value ? parts(value) : null;
  if (!p) return DASH;
  return options.seconds ? `${p.hh}:${p.mm}:${p.ss}` : `${p.hh}:${p.mm}`;
}

/**
 * "21 Nov 2026, 15:57" by default. `seconds` adds ":12", `zone` adds " GMT",
 * `year: false` gives "21 Nov, 15:57".
 */
export function formatDateTime(
  value: string | Date | null | undefined,
  options: { seconds?: boolean; zone?: boolean; year?: boolean } = {},
): string {
  const p = value ? parts(value) : null;
  if (!p) return DASH;
  const date = options.year === false ? `${p.day} ${MONTHS_SHORT[p.month]}` : `${p.day} ${MONTHS_SHORT[p.month]} ${p.year}`;
  const time = options.seconds ? `${p.hh}:${p.mm}:${p.ss}` : `${p.hh}:${p.mm}`;
  return `${date}, ${time}${options.zone ? " GMT" : ""}`;
}

/** "October 2026" */
export function formatMonthYear(value: string | Date | null | undefined): string {
  const p = value ? parts(value) : null;
  return p ? `${MONTHS_LONG[p.month]} ${p.year}` : DASH;
}

/** "October" */
export function formatMonthName(value: string | Date | null | undefined): string {
  const p = value ? parts(value) : null;
  return p ? MONTHS_LONG[p.month] ?? DASH : DASH;
}

/** `YYYY-MM-DD` of an instant, in UTC. */
export function isoDate(value: Date): string {
  return `${value.getUTCFullYear()}-${pad(value.getUTCMonth() + 1)}-${pad(value.getUTCDate())}`;
}

/** Whole calendar days from `a` to `b` (UTC dates). */
export function daysBetween(a: string | Date, b: string | Date): number {
  const da = parseInstant(a);
  const db = parseInstant(b);
  if (!da || !db) return 0;
  const ua = Date.UTC(da.getUTCFullYear(), da.getUTCMonth(), da.getUTCDate());
  const ub = Date.UTC(db.getUTCFullYear(), db.getUTCMonth(), db.getUTCDate());
  return Math.round((ub - ua) / 86_400_000);
}

/** "11:30" today, "Yesterday", otherwise "18 Nov" (or "18 Nov 2025" in another year). */
export function formatRelativeDay(value: string | Date | null | undefined, now: Date = new Date()): string {
  const d = value ? parseInstant(value) : null;
  if (!d) return DASH;
  const diff = daysBetween(d, now);
  if (diff === 0) return formatTime(d);
  if (diff === 1) return "Yesterday";
  return d.getUTCFullYear() === now.getUTCFullYear() ? formatDayMonth(d) : formatDate(d);
}

/** "1,148,206" */
export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(value);
}

const DECIMAL = /^-?(0|[1-9][0-9]*)(\.[0-9]+)?$/;

/**
 * A filed figure: exact decimal text from the API, grouped and shown to two decimal
 * places at most; text (a name, a date a return carries) is shown as filed, and a
 * blank cell as a dash.
 */
export function formatFigure(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return DASH;
  if (!DECIMAL.test(value)) return value;
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 }).format(Number(value));
}

/** How a section's unit reads in a heading: "GHS millions", "%", "count". */
export function unitPhrase(unit: string, currency: string): string {
  switch (unit) {
    case "millions":
    case "thousands":
      return `${currency} ${unit}`;
    case "units":
      return currency;
    case "percent":
      return "%";
    case "count":
      return "count";
    case "text":
      return "text";
    default:
      return unit;
  }
}

/**
 * Percentages arrive from the API on a 0–100 scale (`api_availability_30d`,
 * `on_time_12m`); `decimals` fixes the precision shown.
 */
export function formatPercent(value: number | null | undefined, decimals = 0): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${value.toFixed(decimals)}%`;
}

/** `part` as a share of `whole`, e.g. "93.3%". */
export function formatShare(part: number, whole: number, decimals = 1): string {
  if (!whole) return DASH;
  return formatPercent((part / whole) * 100, decimals);
}

/** "1.8 s" */
export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return DASH;
  return `${Number(value.toFixed(1))} s`;
}

/** "2.9 s" from milliseconds. */
export function formatDurationMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return DASH;
  return formatSeconds(ms / 1000);
}

/** A SHA-256 hex digest in four readable groups. */
export function formatDigest(hex: string): string {
  const clean = hex.trim().toLowerCase();
  const size = clean.length >= 64 ? 16 : 8;
  const groups: string[] = [];
  for (let i = 0; i < clean.length; i += size) groups.push(clean.slice(i, i + size));
  return groups.join(" ");
}

/** "7c1e9a04…b3f2" */
export function shortHash(hex: string): string {
  const clean = hex.trim().toLowerCase();
  return clean.length > 14 ? `${clean.slice(0, 8)}…${clean.slice(-4)}` : clean;
}

/** "KO" for "Kofi Owusu", "RB" for "Riverbend Rural Bank": first and last words. */
export function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N}\s]/gu, " ").split(/\s+/).filter(Boolean);
  const picked = words.length > 1 ? [words[0], words[words.length - 1]] : words;
  return picked.map((w) => w?.[0]?.toUpperCase() ?? "").join("") || "?";
}

/** "Akosua Boateng" → "A. Boateng" */
export function shortPersonName(name: string): string {
  const words = name.trim().split(/\s+/);
  if (words.length < 2) return name.trim();
  return `${words[0]?.[0] ?? ""}. ${words.slice(1).join(" ")}`;
}

/** "scope_and_limit" → "Scope and limit" */
export function humanize(key: string): string {
  const text = key.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim().toLowerCase();
  return text ? text[0]!.toUpperCase() + text.slice(1) : key;
}

/** "1 return", "6 returns" */
export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${formatNumber(count)} ${count === 1 ? singular : pluralForm}`;
}

/** "v2" */
export function formatVersion(version: number): string {
  return `v${version}`;
}

/** "gateway-receipt-2026-q4" → "2026-Q4"; other key ids are shown as given. */
export function formatGatewayKey(kid: string): string {
  const match = /^gateway-(?:receipt|audit)-(\d{4})-(q[1-4])$/i.exec(kid);
  return match ? `${match[1]}-${match[2]!.toUpperCase()}` : kid;
}

/** Splits a message body into paragraphs on blank lines. */
export function paragraphs(body: string): string[] {
  return body
    .split(/\n\s*\n/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/** A value from an audit entry's `detail`, as text. */
export function detailValue(value: unknown): string {
  if (value === null || value === undefined) return DASH;
  if (typeof value === "string") return value;
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return JSON.stringify(value);
}
