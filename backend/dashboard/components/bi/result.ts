"use client";

/**
 * Reading a `BiQueryResult` for display.
 *
 * The result is columns plus positional rows, and every cell may be null. Null
 * means the platform did not measure the thing — not that it measured zero —
 * so every formatter here maps absence to an em dash and nothing in this file
 * substitutes a number for a missing one. A chart built from these helpers
 * plots a gap where there is a gap.
 *
 * Units come from the column's declared `format`, which is the catalogue's own
 * value type, and this module is the ONE place that turns one into text — the
 * charts, the tables and the self-service grid all call `formatCell`, so they
 * cannot disagree about a member. The server's exporters
 * (`app/services/bi/exports/context.py`) apply the same rules to the same value
 * types, which is what makes a figure on screen citable against the audit twin.
 *
 * There is deliberately no catch-all numeric type. The catalogue used to have
 * one — `ratio` — which served a fractional rate, a concentration index and a
 * duration in years at once, and left this module no way to be right about all
 * three: a rate wants x100 and a percent sign, an index and a duration must
 * never be scaled. `ratio` was retired for `fraction`, `index` and
 * `duration_years`, so each is now handled on its own terms:
 *
 * * `amount` — money in the bank's own reporting currency;
 * * `pct` — a percentage the engine already multiplied by a hundred
 *   (`car_pct` arrives as `14.20`);
 * * `fraction` — a proportion of one, shown multiplied by a hundred with a
 *   percent sign, so `0.062` reads as `6.20%` here and in the export;
 * * `index` — dimensionless, on its own scale: never scaled, never given a
 *   percent sign, and it may be negative;
 * * `duration_years` — a span of time, named in years;
 * * `count` — a whole number of things;
 * * `text` / `date` / `flag` — a dimension's values.
 *
 * An unrecognised value type is shown as it arrived rather than coerced: a type
 * added to the catalogue and forgotten here must read oddly for one release,
 * never wrongly.
 */

import type { BiQueryResult, BiResultColumn } from "@aequoros/risk-service-api";
// Relative, not the `@/` alias: this module is compiled and run under node by
// `pnpm --filter @aequoros/dashboard test`, which cannot resolve the alias.
import { currencyCode, fmtCurrency, fmtInt, fmtNum, fmtPct } from "../../lib/format";

/** The display for a value the platform did not measure. */
export const NOT_MEASURED = "—";

/** A `fraction` is a proportion of one; a reader is shown it out of a hundred. */
const FRACTION_SCALE = 100;
/** Decimal places an index carries: enough to separate two close portfolios. */
const INDEX_DECIMALS = 4;
/** Decimal places a duration in years carries. */
const DURATION_DECIMALS = 2;

/**
 * The catalogue value types that are figures — `NUMERIC_VALUE_TYPES` in
 * `app/domain/bi/catalogue/members.py`, plus `int`, which is not a catalogue
 * type but the level marker the grid surfaces add.
 */
const NUMERIC_FORMATS: readonly string[] = [
  "amount",
  "pct",
  "fraction",
  "index",
  "duration_years",
  "count",
  "int",
];

export function dimensionColumns(
  result: Pick<BiQueryResult, "columns">,
): BiResultColumn[] {
  return result.columns.filter((column) => column.kind === "dimension");
}

export function measureColumns(
  result: Pick<BiQueryResult, "columns">,
): BiResultColumn[] {
  return result.columns.filter((column) => column.kind === "measure");
}

/** A cell as a finite number, or null when it is absent or not numeric. */
export function cellNumber(value: unknown): number | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return null;
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/** A cell as display text, in the institution's own units. */
export function formatCell(value: unknown, format: string): string {
  if (value === null || value === undefined || value === "") {
    return NOT_MEASURED;
  }
  if (format === "text") return String(value);
  if (format === "date") return String(value).slice(0, 10);
  if (format === "flag")
    return value === true || value === "true" ? "Yes" : "No";

  const parsed = cellNumber(value);
  if (parsed === null) return String(value);
  if (format === "amount") return fmtCurrency(parsed);
  if (format === "pct") return fmtPct(parsed);
  if (format === "fraction") return fmtPct(parsed * FRACTION_SCALE);
  if (format === "index") return fmtNum(parsed, INDEX_DECIMALS);
  if (format === "duration_years") {
    return `${fmtNum(parsed, DURATION_DECIMALS)} years`;
  }
  if (format === "count" || format === "int") return fmtInt(parsed);
  return String(value);
}

/**
 * The unit a column heading states, so a reader does not have to infer it from
 * the first cell — which may be the one cell that is absent. An amount names the
 * institution's own currency, resolved from the active jurisdiction.
 */
export function columnUnit(format: string): string | null {
  if (format === "amount") return currencyCode();
  if (format === "pct" || format === "fraction") return "%";
  if (format === "duration_years") return "years";
  return null;
}

/** True when a column holds figures, so a surface can right-align them. */
export function isNumericFormat(format: string): boolean {
  return NUMERIC_FORMATS.includes(format);
}

/**
 * The axis label for a row: every dimension value it groups, joined. A row with
 * no dimensions at all is the whole institution.
 */
export function rowLabel(
  result: Pick<BiQueryResult, "columns">,
  row: readonly unknown[],
): string {
  const parts = result.columns
    .map((column, index) => ({ column, index }))
    .filter((entry) => entry.column.kind === "dimension")
    .map((entry) => formatCell(row[entry.index], entry.column.format));
  return parts.length > 0 ? parts.join(" · ") : "Whole institution";
}

/** True when the answer contains no rows at all — the "needs data" case. */
export function isEmptyResult(
  result: Pick<BiQueryResult, "rows"> | null | undefined,
): boolean {
  return !result || result.rows.length === 0;
}

/**
 * True when every measure cell in the answer is ABSENT. The query matched rows
 * but measured nothing, which reads on a chart exactly like a run of zeros, so
 * it is treated as "needs data" too.
 *
 * Absence is null, undefined or an empty string — NOT "could not be read as a
 * number". A measure whose value type is text or a flag has been measured; if
 * unparseable values counted as absent, an answer that is entirely present
 * would render as a data gap.
 */
export function hasNoMeasuredValue(result: BiQueryResult): boolean {
  const indices = result.columns
    .map((column, index) => ({ column, index }))
    .filter((entry) => entry.column.kind === "measure")
    .map((entry) => entry.index);
  if (indices.length === 0) return true;
  const absent = (value: unknown): boolean =>
    value === null || value === undefined || value === "";
  return result.rows.every((row) =>
    indices.every((index) => absent(row[index])),
  );
}
