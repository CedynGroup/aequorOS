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

import type {
  BiCatalogueDimensionRead,
  BiCatalogueRead,
  BiFilter,
  BiQuery,
  BiTime,
} from "@aequoros/risk-service-api";

import { sliceableDimensions } from "./exploreQuery";

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

/**
 * The page's narrowing, applied only to the widgets that can actually carry it.
 *
 * NOT EVERY FIGURE CAN BE SLICED BY EVERY FIELD, and the server is right to
 * refuse the ones that cannot. A measure declares `allowed_dimensions`, and an
 * ENGINE-COPIED figure — net interest margin, a capital ratio — is one
 * institution-level number the platform copies from the engine rather than
 * recomputing, so it has no decomposition by deposit account type and no honest
 * answer to "the same figure, for retail only". A filter counts as a slice, so
 * narrowing such a widget is refused with
 * `<measure> cannot be sliced by <dimension>` — which is what the reader saw:
 * pick one field in the filter bar and every engine-metric tile on the pack
 * breaks at once, because the bar offered a field from the CATALOGUE and the
 * canvas then applied it to EVERY widget.
 *
 * So the applicability is decided here, per widget, from the catalogue the bar
 * itself was built from. The alternative — letting the server drop a filter it
 * cannot apply — is the worse failure: an institution-wide figure would sit
 * beside narrowed ones with nothing saying so, and be read as the narrowed
 * number. That is the same defect as rendering a missing figure as zero, and
 * the reason a withheld filter must be NAMED to the reader rather than quietly
 * skipped.
 *
 * A measure the catalogue does not describe withholds the filter rather than
 * gambling on it. That costs nothing in practice: a filter can only be added
 * through the bar, the bar is built from the catalogue, so by the time any
 * filter exists the catalogue has loaded — and with no filters this returns
 * before it looks at anything.
 *
 * Returns the query to submit and the human LABELS of the fields it could not
 * be narrowed by, for the widget to state.
 */
export function applicableNarrowing(
  query: BiQuery,
  filters: readonly BiFilter[],
  catalogue: BiCatalogueRead | undefined,
): { query: BiQuery; withheld: readonly string[] } {
  if (filters.length === 0) {
    return { query: narrowedQuery(query, filters), withheld: [] };
  }
  // The SAME rule Explore enforces before it sends a question, not a second
  // copy of it: a dimension is sliceable only if EVERY measure in the query
  // allows it. Explore has guarded this since it shipped; the certified-pack
  // canvas did not, which is the whole defect.
  const sliceable = sliceableDimensions(
    {
      measures: catalogue?.measures ?? [],
      dimensions: catalogue?.dimensions ?? [],
    },
    query.measures,
  );
  const byId = new Map(sliceable.map((dimension) => [dimension.id, dimension]));

  const applied: BiFilter[] = [];
  const withheld: string[] = [];
  for (const filter of filters) {
    if (byId.has(filter.member)) {
      applied.push(filter);
      continue;
    }
    const known = (catalogue?.dimensions ?? []).find(
      (entry) => entry.id === filter.member,
    );
    withheld.push(known?.label ?? filter.member);
  }
  return { query: narrowedQuery(query, applied), withheld };
}

/**
 * The fields a DASHBOARD may be narrowed by — not the fields the reader's
 * catalogue contains.
 *
 * The filter bar used to offer every dimension with a closed vocabulary that
 * the caller could read, which is the right list for Explore (where the reader
 * is building the question) and the wrong one for a published dashboard (where
 * someone else already did). On an ALCO pack it offered deposit and loan fields
 * alike, so choosing one narrowed a few tiles, did not apply to several, and
 * matched no row at all on the rest — a stakeholder opening the page reads that
 * as a broken dashboard, and they are right to.
 *
 * A field is offered when AT LEAST ONE widget on the page can be sliced by it.
 * Not every widget: an engine copy can be sliced by nothing, and requiring all
 * of them would leave most packs with an empty filter bar. That is also how
 * Power BI and Tableau behave — a page filter applies to the visuals that
 * support it — and it is why `applicableNarrowing` still has to name the tiles
 * a chosen field did not reach.
 */
export function narrowingFieldsFor(
  catalogue: BiCatalogueRead | undefined,
  widgetMeasures: readonly (readonly string[])[],
): BiCatalogueDimensionRead[] {
  const closed = (catalogue?.dimensions ?? []).filter(
    (dimension) => (dimension.values ?? []).length > 0,
  );
  if (widgetMeasures.length === 0) return closed;
  const reachable = new Set<string>();
  for (const measures of widgetMeasures) {
    for (const dimension of sliceableDimensions(
      {
        measures: catalogue?.measures ?? [],
        dimensions: catalogue?.dimensions ?? [],
      },
      measures,
    )) {
      reachable.add(dimension.id);
    }
  }
  return closed.filter((dimension) => reachable.has(dimension.id));
}
