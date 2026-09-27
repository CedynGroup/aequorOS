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
 * value type. `ratio` is deliberately rendered as the fraction the server sent,
 * unscaled: the catalogue uses that type both for a rate expressed as a
 * fraction and for a concentration index, and multiplying either by a hundred
 * to make it "look like a percentage" would be a display that disagrees with
 * the figure. Percentages arrive already percentage-scaled, as `pct`.
 */

import type { BiQueryResult, BiResultColumn } from "@aequoros/risk-service-api";
import { fmtCurrency, fmtInt, fmtPct } from "@/lib/format";

/** The display for a value the platform did not measure. */
export const NOT_MEASURED = "—";

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
  if (format === "count" || format === "int") return fmtInt(parsed);
  if (format === "ratio") return parsed.toFixed(4);
  return String(value);
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
