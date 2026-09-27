/**
 * Turning the Explore controls into a `BiQuery` the server will not refuse.
 *
 * The compiler is deny-by-default and it refuses a malformed question with a
 * 422 rather than a partial answer. That is the right server behaviour, and it
 * makes one thing the CLIENT's job: a control that can build a query the
 * compiler rejects is a bug in the control, not an error the reader should be
 * shown. So every rule the compiler enforces on the SHAPE of a query — as
 * opposed to on the reader's authority, which only the server can decide — is
 * enforced here first, and the offending combination is explained in words the
 * reader can act on instead of being sent.
 *
 * The rules mirrored here, each from `app/services/bi/compiler.py`:
 *
 * * measures must share one time behaviour (a point-in-time balance and a
 *   period flow cannot be measured in one answer);
 * * every row field, pivot field and filter field must be one the chosen
 *   measures can be sliced by;
 * * the pivot names ONE field, it is never also a row field or the Top-N
 *   field, a concentration measure cannot be pivoted, and the field's
 *   vocabulary must fit inside the server's column cap — the compiler REFUSES
 *   a pivot whose field has more values in the window than the cap, it does
 *   not silently drop columns, so offering such a field would guarantee a
 *   refusal;
 * * Top-N names one of the chosen row fields;
 * * a sort names a chosen measure or row field, and a pivoted answer cannot be
 *   sorted by a measure;
 * * every list obeys the request cap `app/schemas/bi.py` declares.
 *
 * TWO RULES STAY SERVER-SIDE, deliberately. The compiler also refuses measures
 * that read from different fact tables, and two concentration measures taken
 * over different fields. Neither the fact table nor a concentration's own
 * `over` field is published in `GET …/bi/catalogue`, so this module cannot
 * decide either without inventing knowledge; those two arrive as a refusal the
 * page renders as a refusal.
 *
 * Pure and React-free so `exploreQuery.test.ts` can prove the properties under
 * node, including against the server's own constants.
 */

import type {
  BiCatalogueDimensionRead,
  BiCatalogueMeasureRead,
  BiFilter,
  BiQuery,
  BiSort,
  BiTime,
} from "@aequoros/risk-service-api";

// --- the request caps, mirrored from app/schemas/bi.py ----------------------
//
// The server is the authority on every one of these; `exploreQuery.test.ts`
// reads `app/schemas/bi.py` and fails if a value here has drifted. They are
// mirrored rather than fetched because a control has to know its bounds before
// it renders, and a cap the client guessed low would hide a legal question.

/** `BI_MAX_MEASURES` — measures in one question. */
export const BI_MEASURE_CAP = 25;
/** `BI_MAX_DIMENSIONS` — row fields in one question. */
export const BI_DIMENSION_CAP = 12;
/** `BI_MAX_FILTERS` — filters in one question. */
export const BI_FILTER_CAP = 40;
/** `BI_MAX_SORTS` — sorted columns in one question. */
export const BI_SORT_CAP = 8;
/** `BI_TOP_N_MAX` — groups kept before the remainder row. */
export const BI_TOP_N_CAP = 50;
/** `BI_PIVOT_MAX_COLUMNS` — value columns a pivot may emit. */
export const BI_PIVOT_COLUMN_CAP = 50;

/** Aggregations that answer for a whole slice at once and cannot be pivoted. */
const CONCENTRATION_AGGREGATIONS: readonly string[] = ["hhi", "top_n_share"];

/** How a measure's time behaviour reads on screen. */
const TIME_BEHAVIOUR_LABELS: Readonly<Record<string, string>> = {
  stock: "position on the date",
  flow: "movement over the period",
};

export function timeBehaviourLabel(code: string): string {
  return TIME_BEHAVIOUR_LABELS[code] ?? code;
}

/** What the Explore controls currently say the question is. */
export type ExploreShape = Readonly<{
  measures: readonly string[];
  /** Row fields, outermost first. */
  dimensions: readonly string[];
  filters: readonly BiFilter[];
  /** The one field spread across the columns, or null. */
  pivot: string | null;
  /** Keep the largest groups of one row field, or null for all of them. */
  topN: Readonly<{ dimension: string; n: number; other: boolean }> | null;
  /** Roll the row fields up with server-computed subtotals. */
  subtotals: boolean;
  sort: readonly BiSort[];
}>;

/** The catalogue facts the shape is checked against. */
export type ExploreCatalogue = Readonly<{
  measures: readonly BiCatalogueMeasureRead[];
  dimensions: readonly BiCatalogueDimensionRead[];
}>;

/**
 * Something the reader has to know: either the question cannot be asked at all
 * (`blocking`), or part of it was set aside and the answer is narrower than the
 * controls suggest. Nothing is ever dropped silently.
 */
export type ExploreProblem = Readonly<{
  id: string;
  message: string;
  blocking: boolean;
}>;

export type ExplorePlan = Readonly<{
  /** The query to submit, or null when a blocking problem stands in the way. */
  query: BiQuery | null;
  problems: readonly ExploreProblem[];
}>;

export const EMPTY_SHAPE: ExploreShape = {
  measures: [],
  dimensions: [],
  filters: [],
  pivot: null,
  topN: null,
  subtotals: false,
  sort: [],
};

function measureById(
  catalogue: ExploreCatalogue,
  id: string,
): BiCatalogueMeasureRead | undefined {
  return catalogue.measures.find((measure) => measure.id === id);
}

function dimensionById(
  catalogue: ExploreCatalogue,
  id: string,
): BiCatalogueDimensionRead | undefined {
  return catalogue.dimensions.find((dimension) => dimension.id === id);
}

function labelOfMeasure(catalogue: ExploreCatalogue, id: string): string {
  return measureById(catalogue, id)?.label ?? id;
}

function labelOfDimension(catalogue: ExploreCatalogue, id: string): string {
  return dimensionById(catalogue, id)?.label ?? id;
}

/**
 * Row fields the chosen measures can all be sliced by.
 *
 * The compiler requires every dimension to be in EVERY chosen measure's
 * `allowed_dimensions`, so the intersection is the honest offer — a union would
 * put a field on screen that refuses the moment it is used.
 */
export function sliceableDimensions(
  catalogue: ExploreCatalogue,
  measures: readonly string[],
): BiCatalogueDimensionRead[] {
  if (measures.length === 0) return [];
  const chosen = measures
    .map((id) => measureById(catalogue, id))
    .filter((measure): measure is BiCatalogueMeasureRead => Boolean(measure));
  if (chosen.length === 0) return [];
  return catalogue.dimensions.filter((dimension) =>
    chosen.every((measure) =>
      (measure.allowedDimensions ?? []).includes(dimension.id),
    ),
  );
}

/**
 * Whether a field may be spread across the columns.
 *
 * A pivot needs a CLOSED vocabulary small enough to fit the server's column
 * cap. The compiler counts the field's distinct values in the reporting window
 * and refuses outright above the cap, so a field whose published vocabulary is
 * absent (an open field such as a reference) or larger than the cap can only
 * produce a refusal, and is not offered.
 */
export function pivotableDimensions(
  catalogue: ExploreCatalogue,
  measures: readonly string[],
): BiCatalogueDimensionRead[] {
  return sliceableDimensions(catalogue, measures).filter((dimension) => {
    const size = (dimension.values ?? []).length;
    return size > 0 && size <= BI_PIVOT_COLUMN_CAP;
  });
}

/** True when any chosen measure answers for a whole slice at once. */
export function hasConcentrationMeasure(
  catalogue: ExploreCatalogue,
  measures: readonly string[],
): boolean {
  return measures.some((id) => {
    const measure = measureById(catalogue, id);
    return (
      measure !== undefined &&
      CONCENTRATION_AGGREGATIONS.includes(measure.aggregation)
    );
  });
}

/**
 * The time behaviours the chosen measures span. One is required; more than one
 * is the "stock and flow cannot share a query" refusal, prevented here.
 */
export function timeBehavioursOf(
  catalogue: ExploreCatalogue,
  measures: readonly string[],
): string[] {
  const seen = new Set<string>();
  for (const id of measures) {
    const measure = measureById(catalogue, id);
    if (measure) seen.add(measure.timeBehaviour);
  }
  return [...seen];
}

/**
 * Whether a measure may be ADDED to what is already chosen. Used to disable the
 * option rather than to explain a refusal after the fact.
 */
export function measureIsCompatible(
  catalogue: ExploreCatalogue,
  measures: readonly string[],
  candidate: string,
): boolean {
  if (measures.includes(candidate)) return true;
  if (measures.length >= BI_MEASURE_CAP) return false;
  const measure = measureById(catalogue, candidate);
  if (!measure) return false;
  const behaviours = timeBehavioursOf(catalogue, measures);
  return behaviours.length === 0 || behaviours[0] === measure.timeBehaviour;
}

/**
 * Sorts reduced to the ones the compiler accepts, and how many were set aside.
 *
 * Shared by the shaping controls and by the grid's own header sorting, so a
 * sort asked for in either place is held to one rule.
 */
export function legalSorts(
  requested: readonly BiSort[],
  context: Readonly<{
    measures: readonly string[];
    dimensions: readonly string[];
    pivoted: boolean;
  }>,
): Readonly<{ sort: BiSort[]; droppedMeasureSort: boolean; droppedOverCap: number }> {
  const known = new Set<string>([...context.measures, ...context.dimensions]);
  const measures = new Set<string>(context.measures);
  let droppedMeasureSort = false;
  const kept: BiSort[] = [];
  for (const entry of requested) {
    if (!known.has(entry.member)) continue;
    if (context.pivoted && measures.has(entry.member)) {
      droppedMeasureSort = true;
      continue;
    }
    if (kept.some((existing) => existing.member === entry.member)) continue;
    kept.push(entry);
  }
  return {
    sort: kept.slice(0, BI_SORT_CAP),
    droppedMeasureSort,
    droppedOverCap: Math.max(0, kept.length - BI_SORT_CAP),
  };
}

/**
 * The question the controls describe, as the compiler will take it — or the
 * reason it cannot be asked yet.
 */
export function buildExploreQuery(
  shape: ExploreShape,
  catalogue: ExploreCatalogue,
  time: BiTime,
): ExplorePlan {
  const problems: ExploreProblem[] = [];
  const add = (id: string, message: string, blocking = false) => {
    problems.push({ id, message, blocking });
  };

  const measures = shape.measures.filter((id) =>
    Boolean(measureById(catalogue, id)),
  );
  if (measures.length === 0) {
    add(
      "no-measure",
      "Choose at least one measure. Nothing is asked of the server until you do.",
      true,
    );
    return { query: null, problems };
  }
  if (measures.length > BI_MEASURE_CAP) {
    add(
      "measure-cap",
      `One question can carry up to ${BI_MEASURE_CAP} measures. Remove some, or ask a second question.`,
      true,
    );
  }

  const behaviours = timeBehavioursOf(catalogue, measures);
  if (behaviours.length > 1) {
    const named = behaviours.map(timeBehaviourLabel).join(" and ");
    add(
      "mixed-time-behaviour",
      `These measures are reported differently — ${named} — and cannot share one answer. Keep measures of one kind.`,
      true,
    );
  }

  const sliceable = new Set(
    sliceableDimensions(catalogue, measures).map((dimension) => dimension.id),
  );

  // The pivot field is resolved first: it is never also a row field, so it has
  // to be known before the row fields are settled.
  let pivot: string | null = shape.pivot;
  if (pivot !== null && !sliceable.has(pivot)) {
    add(
      "pivot-not-sliceable",
      `${labelOfDimension(catalogue, pivot)} cannot break down the measures you chose, so it is not spread across the columns.`,
    );
    pivot = null;
  }
  if (pivot !== null) {
    const values = (dimensionById(catalogue, pivot)?.values ?? []).length;
    if (values === 0 || values > BI_PIVOT_COLUMN_CAP) {
      add(
        "pivot-too-wide",
        `${labelOfDimension(catalogue, pivot)} has more values than a column layout can carry — up to ${BI_PIVOT_COLUMN_CAP} columns are shown. Spread a broader field across the columns, or filter this one first.`,
        true,
      );
    }
  }
  if (pivot !== null && hasConcentrationMeasure(catalogue, measures)) {
    add(
      "pivot-concentration",
      "A concentration measure is one figure for the whole slice, so it cannot be spread across columns. Remove it, or remove the column field.",
      true,
    );
  }

  const seenDimension = new Set<string>();
  const dimensions: string[] = [];
  let droppedDimension = false;
  let pivotWasARowField = false;
  for (const id of shape.dimensions) {
    if (seenDimension.has(id)) continue;
    seenDimension.add(id);
    if (id === pivot) {
      pivotWasARowField = true;
      continue;
    }
    if (!sliceable.has(id)) {
      droppedDimension = true;
      continue;
    }
    dimensions.push(id);
  }
  if (droppedDimension) {
    add(
      "dimension-not-sliceable",
      "A break-down field that the chosen measures cannot be split by was set aside. Only fields offered for these measures are used.",
    );
  }
  if (pivotWasARowField) {
    add(
      "pivot-was-a-row-field",
      `${labelOfDimension(catalogue, pivot ?? "")} is across the columns, so it is no longer one of the rows.`,
    );
  }
  if (dimensions.length > BI_DIMENSION_CAP) {
    add(
      "dimension-cap",
      `One question can break down by up to ${BI_DIMENSION_CAP} fields. Remove some to see an answer.`,
      true,
    );
  }
  if (pivot !== null && dimensions.length === 0) {
    add(
      "pivot-needs-rows",
      "Choose at least one field for the rows before spreading a field across the columns.",
      true,
    );
  }

  if (shape.filters.length > BI_FILTER_CAP) {
    add(
      "filter-cap",
      `One question can carry up to ${BI_FILTER_CAP} filters. Remove some to see an answer.`,
      true,
    );
  }

  let topN: ExploreShape["topN"] = shape.topN;
  if (topN !== null && !dimensions.includes(topN.dimension)) {
    add(
      "top-n-not-a-row-field",
      `${labelOfDimension(catalogue, topN.dimension)} is not one of the row fields, so every group is shown rather than the largest few.`,
    );
    topN = null;
  }
  if (topN !== null && (topN.n < 1 || topN.n > BI_TOP_N_CAP)) {
    add(
      "top-n-cap",
      `Between 1 and ${BI_TOP_N_CAP} groups can be kept before the remainder row.`,
      true,
    );
  }

  const sorts = legalSorts(shape.sort, {
    measures,
    dimensions,
    pivoted: pivot !== null,
  });
  if (sorts.droppedMeasureSort) {
    add(
      "sort-measure-under-pivot",
      "An answer spread across columns is ordered by its row fields, not by a figure. The sort on a measure was set aside.",
    );
  }
  if (sorts.droppedOverCap > 0) {
    add(
      "sort-cap",
      `An answer can be ordered by up to ${BI_SORT_CAP} columns. The later ones were set aside.`,
    );
  }

  if (problems.some((problem) => problem.blocking)) {
    return { query: null, problems };
  }

  const query: BiQuery = {
    measures: [...measures],
    dimensions,
    filters: [...shape.filters],
    time,
    sort: sorts.sort,
    subtotals: shape.subtotals && dimensions.length > 0,
  };
  if (pivot !== null) {
    query.pivot = { dimension: pivot, maxColumns: BI_PIVOT_COLUMN_CAP };
  }
  if (topN !== null) {
    query.topN = { dimension: topN.dimension, n: topN.n, other: topN.other };
  }
  return { query, problems };
}
