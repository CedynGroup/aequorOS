/**
 * From a figure to the rows behind it.
 *
 * A reader who follows a BI figure into the loan book or the position blotter
 * must land on THE SAME SLICE the figure measured. That is the whole property
 * this module defends, and it defends it by refusing rather than by
 * approximating: a destination is offered only when every part of the slice maps
 * to a filter that destination applies EXACTLY. A link that lands on a wider
 * book is worse than no link at all, because the reader believes they are
 * looking at the figure's rows and the heading agrees with them.
 *
 * Three ways a slice is not carryable, each of which withholds the destination:
 *
 *  1. it is not one value per field — a multi-value `in`, a negation, a range;
 *  2. a group has no value at all (the "not stated" group is not a filter value);
 *  3. a field maps to no exact filter on that destination.
 *
 * Case 3 is why the search boxes are not used as filters. `?q=` on both
 * destinations is a case-insensitive SUBSTRING match on the reference, so
 * `q=LOAN/1` also matches `LOAN/10`: carrying a reference that way would show a
 * superset under an exact heading. The position blotter instead takes `?ref=`,
 * which selects the one row and opens its lineage; the loan book has no exact
 * reference filter, so a reference-pinned slice simply does not offer it.
 *
 * Pure on purpose — no React, no fetching — so the mapping is provable. See
 * `drill.test.ts`.
 */

import type {
  BiFilter,
  BiQuery,
  BiQueryResult,
} from "@aequoros/risk-service-api";
// Relative, not the `@/` alias: this module is compiled and run under plain
// node by `drill.test.ts`, which cannot resolve the bundler's path alias.
import { isoDay } from "../../lib/api/biKeys";

/** Where a drill can go. Each id is one destination page. */
export type DrillDestinationId = "loan_book" | "positions";

export type DrillDestination = Readonly<{
  id: DrillDestinationId;
  /** The action's label, as the reader reads it. */
  label: string;
  href: string;
}>;

/** One field of a slice, pinned to exactly one value. */
export type PinnedMember = Readonly<{ member: string; value: string }>;

export type DrillSlice = Readonly<{
  /** Every field the slice pins, filters first, then the row's own groups. */
  members: readonly PinnedMember[];
  /** The reporting date the figure was measured on, when the query names one. */
  asOf: string | null;
  /**
   * False when some part of the narrowing cannot be expressed as one value per
   * field. An inexact slice carries no destination — see the module comment.
   */
  exact: boolean;
}>;

/**
 * Catalogue member id → the loan book's own query parameter.
 *
 * Every entry is a filter `/credit/book` applies server-side and exactly. The
 * loan book classifies the whole book in memory, so `sector`, `stage` and
 * `dpd_band` are read off the classified row itself — the same values this
 * mapping's members are grouped by.
 */
const LOAN_BOOK_PARAMS: Readonly<Record<string, string>> = {
  "loan.grade": "grade",
  "loan.sector": "sector",
  "loan.ifrs9_stage": "stage",
  "loan.dpd_band": "dpd_band",
  "product.code": "product",
  "branch.code": "branch",
};

/**
 * Catalogue member id → the position blotter's own query parameter.
 *
 * `position.source_reference` maps to `ref`, which selects one row and opens its
 * lineage drawer, never to the substring search.
 */
const POSITIONS_PARAMS: Readonly<Record<string, string>> = {
  "position.type": "type",
  "position.currency": "ccy",
  "position.source_reference": "ref",
};

const LOAN_BOOK_PATH = "/credit/book";
const POSITIONS_PATH = "/positions";

/** A value that can stand as one URL parameter, or null when it cannot. */
function pinnedValue(value: unknown): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number")
    return Number.isFinite(value) ? String(value) : null;
  if (typeof value === "string") return value;
  return null;
}

/**
 * The single value a filter pins its field to, or null when it pins none.
 *
 * Only `eq` and a one-value `in` pin a field. `ne`/`not_in` EXCLUDE, and the
 * destinations have no exclusion filter; the comparisons and ranges name no
 * value at all. Dropping any of them would widen the destination's book.
 */
function pinnedFilterValue(filter: BiFilter): string | null {
  if (filter.op !== "eq" && filter.op !== "in") return null;
  const values = filter.values ?? [];
  if (values.length !== 1) return null;
  return pinnedValue(values[0]);
}

/**
 * The slice one table row stands for: the query's own narrowing plus the row's
 * group, with the reporting date the figure was measured on.
 */
export function sliceForRow(
  result: Pick<BiQueryResult, "columns">,
  row: readonly unknown[],
  query: BiQuery,
): DrillSlice {
  const pinned = new Map<string, string>();
  let exact = true;

  for (const filter of query.filters ?? []) {
    const value = pinnedFilterValue(filter);
    if (value === null) {
      exact = false;
      continue;
    }
    pinned.set(filter.member, value);
  }

  result.columns.forEach((column, index) => {
    if (column.kind !== "dimension") return;
    const member = column.memberId;
    if (typeof member !== "string" || member.length === 0) {
      exact = false;
      return;
    }
    const value = pinnedValue(row[index]);
    if (value === null) {
      // The "not stated" group. No destination has an is-absent filter, so
      // carrying this row would silently widen the page to every group.
      exact = false;
      return;
    }
    // The row's own group is at least as narrow as any filter on the same
    // field, so it wins where both name one.
    pinned.set(member, value);
  });

  return {
    members: [...pinned].map(([member, value]) => ({ member, value })),
    asOf: isoDay(query.time?.asOf),
    exact,
  };
}

/**
 * True when the answer is about LOANS.
 *
 * The destination follows the figure, not the row: a deposits-by-branch table
 * pins a branch the loan book would happily filter on, and offering it would
 * answer a question about deposits with a page of loans. Loan measures and loan
 * dimensions both live in the `loans.` / `loan.` namespaces, and a query naming
 * either is a question about the loan book.
 */
function namesLoans(query: BiQuery): boolean {
  const named = [
    ...query.measures,
    ...(query.dimensions ?? []),
    ...(query.filters ?? []).map((filter) => filter.member),
  ];
  return named.some((id) => id.startsWith("loans.") || id.startsWith("loan."));
}

function hrefFor(
  path: string,
  slice: DrillSlice,
  mapping: Readonly<Record<string, string>>,
): string | null {
  if (!slice.exact || slice.members.length === 0) return null;
  const search = new URLSearchParams();
  for (const { member, value } of slice.members) {
    const parameter = mapping[member];
    // This destination has no exact filter for the field. Withhold it rather
    // than drop the field and show a wider book under the figure's heading.
    if (parameter === undefined) return null;
    search.set(parameter, value);
  }
  // Both destinations read the book AS OF an exact date, so the date the
  // figure was measured on travels with the slice.
  if (slice.asOf !== null) search.set("as_of", slice.asOf);
  return `${path}?${search.toString()}`;
}

/**
 * The destinations that can honour this row's slice exactly — in practice one,
 * because the loan book and the position blotter answer different questions.
 *
 * An empty result is the honest outcome for a slice neither page can reproduce,
 * and the caller renders no action at all rather than a link that lies.
 */
export function drillDestinations(
  result: Pick<BiQueryResult, "columns">,
  row: readonly unknown[],
  query: BiQuery,
): readonly DrillDestination[] {
  const slice = sliceForRow(result, row, query);
  if (namesLoans(query)) {
    const href = hrefFor(LOAN_BOOK_PATH, slice, LOAN_BOOK_PARAMS);
    return href === null
      ? []
      : [{ id: "loan_book", label: "Open in Loan Book", href }];
  }
  const href = hrefFor(POSITIONS_PATH, slice, POSITIONS_PARAMS);
  return href === null
    ? []
    : [{ id: "positions", label: "Open in Positions", href }];
}
