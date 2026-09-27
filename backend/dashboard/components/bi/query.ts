"use client";

/**
 * Building the `BiQuery` a surface actually submits.
 *
 * A widget declares WHAT it measures; the page declares WHEN and, through the
 * filter bar, OVER WHICH SLICE. This module merges the two, and it is the only
 * place that does, so a pack widget and an Explore query are narrowed the same
 * way.
 *
 * The page's filters win over a widget's own on the same field. That is the
 * safe direction: narrowing is what the reader asked for, and the server
 * authorizes every filter member regardless of who put it there — a filter the
 * caller may not read is refused whether it came from a pack or from the bar.
 */

import type { BiFilter, BiQuery, BiTime } from "@aequoros/risk-service-api";
import { utcDay } from "@/lib/api/biKeys";

export type BiWindowSelection = Readonly<{
  /** ISO `YYYY-MM-DD`. */
  asOf: string;
  /** ISO `YYYY-MM-DD`, or null for no comparison. */
  compareTo?: string | null;
}>;

export function biTimeFor(selection: BiWindowSelection): BiTime {
  return {
    asOf: selection.asOf,
    compareTo: selection.compareTo ? selection.compareTo : undefined,
  };
}

/** The same window, as the trust route wants it. */
export function trustDateFor(selection: BiWindowSelection): Date {
  return utcDay(selection.asOf);
}

export function mergeFilters(
  base: readonly BiFilter[] | undefined,
  overrides: readonly BiFilter[],
): BiFilter[] {
  const overridden = new Set(overrides.map((filter) => filter.member));
  return [
    ...(base ?? []).filter((filter) => !overridden.has(filter.member)),
    ...overrides,
  ];
}

/** A widget's declared query, narrowed to the page's window and slice. */
export function effectiveQuery(
  query: BiQuery,
  selection: BiWindowSelection,
  filters: readonly BiFilter[],
): BiQuery {
  return {
    ...query,
    time: biTimeFor(selection),
    filters: mergeFilters(query.filters, filters),
  };
}

/**
 * A query whose WINDOW is already settled, narrowed only by the page's filters.
 *
 * This is what a certified pack's widget needs and `effectiveQuery` is not. A
 * pack widget declares its window RELATIVE to the reporting date — month to date,
 * trailing twelve months, the prior quarter — and the server resolves that
 * relative window into a `BiTime` when it serves the pack for the date the reader
 * asked about. Overwriting it with the page's single date would silently turn a
 * twelve-month trend into a point read and a year-to-date total into one day's,
 * and the chart would still draw, headed with the title the pack authored.
 *
 * So the date reaches a pack by being the date the PACK was requested for, and
 * the only thing merged in afterwards is the reader's own narrowing — which the
 * server authorizes member by member regardless of who added it.
 */
export function narrowedQuery(
  query: BiQuery,
  filters: readonly BiFilter[],
): BiQuery {
  return { ...query, filters: mergeFilters(query.filters, filters) };
}
