/**
 * The Explore controls cannot build a question the compiler refuses.
 *
 * `app/services/bi/compiler.py` is deny-by-default and answers a malformed
 * query with a 422 and no rows. That is correct on the server and it makes one
 * thing the client's job: every rule about the SHAPE of a query has to be
 * enforced before the request is sent, or a reader clicks a control and gets an
 * error instead of an answer. Each case below is one of those rules, named for
 * the compiler refusal it prevents.
 *
 * The caps are checked against `app/schemas/bi.py` itself, not against a copy of
 * it, because a cap the client believes is smaller than the server's would hide
 * a legal question, and one it believes is larger would send a refused one.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";

import {
  BI_DIMENSION_CAP,
  BI_FILTER_CAP,
  BI_MEASURE_CAP,
  BI_PIVOT_COLUMN_CAP,
  BI_SORT_CAP,
  BI_TOP_N_CAP,
  buildExploreQuery,
  EMPTY_SHAPE,
  hasConcentrationMeasure,
  legalSorts,
  measureIsCompatible,
  pivotableDimensions,
  sliceableDimensions,
  type ExploreCatalogue,
  type ExploreShape,
} from "./exploreQuery";

let failures = 0;
function test(name: string, fn: () => void): void {
  try {
    fn();
  } catch (error) {
    failures += 1;
    console.error(`FAIL ${name}`);
    console.error(error);
  }
}

// --- the caps are the server's -----------------------------------------------

function repoRoot(): string {
  let dir = __dirname;
  for (let index = 0; index < 10; index += 1) {
    if (existsSync(join(dir, "backend", "app"))) return dir;
    const parent = dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  throw new Error(`could not locate the repository root from ${__dirname}`);
}

const SCHEMA_SOURCE = readFileSync(
  join(repoRoot(), "backend", "app", "schemas", "bi.py"),
  "utf8",
);

function serverCap(name: string): number {
  const match = new RegExp(`^${name}\\s*=\\s*(\\d+)`, "m").exec(SCHEMA_SOURCE);
  assert.ok(match, `${name} is no longer declared in app/schemas/bi.py`);
  return Number(match![1]);
}

test("every request cap mirrors app/schemas/bi.py", () => {
  assert.equal(BI_MEASURE_CAP, serverCap("BI_MAX_MEASURES"));
  assert.equal(BI_DIMENSION_CAP, serverCap("BI_MAX_DIMENSIONS"));
  assert.equal(BI_FILTER_CAP, serverCap("BI_MAX_FILTERS"));
  assert.equal(BI_SORT_CAP, serverCap("BI_MAX_SORTS"));
  assert.equal(BI_TOP_N_CAP, serverCap("BI_TOP_N_MAX"));
  assert.equal(BI_PIVOT_COLUMN_CAP, serverCap("BI_PIVOT_MAX_COLUMNS"));
});

// --- a catalogue to ask questions of ----------------------------------------

function measure(
  id: string,
  extra: Partial<{
    aggregation: string;
    timeBehaviour: string;
    allowedDimensions: string[];
  }> = {},
) {
  return {
    id,
    label: id,
    description: "",
    module: "credit",
    sensitivity: "aggregated",
    measureKind: "portfolio",
    aggregation: extra.aggregation ?? "sum",
    timeBehaviour: extra.timeBehaviour ?? "stock",
    valueType: "amount",
    grain: "portfolio",
    allowedDimensions: extra.allowedDimensions ?? ["grade", "branch", "wide"],
    favourableDirection: "neutral",
    certified: false,
  };
}

function dimension(id: string, valueCount: number) {
  return {
    id,
    label: id,
    description: "",
    module: "credit",
    sensitivity: "aggregated",
    valueType: "text",
    values: Array.from({ length: valueCount }, (_, index) => ({
      code: `c${index}`,
      label: `c${index}`,
    })),
  };
}

const CATALOGUE: ExploreCatalogue = {
  measures: [
    measure("loans.balance"),
    measure("loans.flow", { timeBehaviour: "flow" }),
    measure("loans.hhi", { aggregation: "hhi" }),
    measure("loans.narrow", { allowedDimensions: ["grade"] }),
  ],
  dimensions: [
    dimension("grade", 8),
    dimension("branch", 12),
    dimension("wide", BI_PIVOT_COLUMN_CAP + 1),
    dimension("open", 0),
  ],
};

const TIME = { asOf: "2026-06-30" };

function shapeOf(patch: Partial<ExploreShape>): ExploreShape {
  return { ...EMPTY_SHAPE, ...patch };
}

function problemIds(shape: ExploreShape): string[] {
  return buildExploreQuery(shape, CATALOGUE, TIME).problems.map(
    (problem) => problem.id,
  );
}

// --- the offer itself is narrowed -------------------------------------------

test("only fields EVERY chosen measure can be sliced by are offered", () => {
  const both = sliceableDimensions(CATALOGUE, [
    "loans.balance",
    "loans.narrow",
  ]).map((entry) => entry.id);
  assert.deepEqual(both, ["grade"]);
  assert.deepEqual(sliceableDimensions(CATALOGUE, []), []);
});

test("a field with no vocabulary, or one wider than the column cap, is not pivotable", () => {
  const offered = pivotableDimensions(CATALOGUE, ["loans.balance"]).map(
    (entry) => entry.id,
  );
  assert.deepEqual(offered, ["grade", "branch"]);
});

test("a measure of the other time behaviour cannot be added", () => {
  assert.equal(
    measureIsCompatible(CATALOGUE, ["loans.balance"], "loans.flow"),
    false,
  );
  assert.equal(
    measureIsCompatible(CATALOGUE, ["loans.balance"], "loans.hhi"),
    true,
  );
  assert.equal(measureIsCompatible(CATALOGUE, [], "loans.flow"), true);
});

test("a concentration aggregation is recognised from the catalogue", () => {
  assert.equal(hasConcentrationMeasure(CATALOGUE, ["loans.hhi"]), true);
  assert.equal(hasConcentrationMeasure(CATALOGUE, ["loans.balance"]), false);
});

// --- and the builder refuses what slipped through ----------------------------

test("no measure means no query, and says so", () => {
  const plan = buildExploreQuery(EMPTY_SHAPE, CATALOGUE, TIME);
  assert.equal(plan.query, null);
  assert.deepEqual(
    plan.problems.map((problem) => problem.id),
    ["no-measure"],
  );
});

test("stock and flow measures never reach the compiler together", () => {
  const plan = buildExploreQuery(
    shapeOf({ measures: ["loans.balance", "loans.flow"] }),
    CATALOGUE,
    TIME,
  );
  assert.equal(plan.query, null);
  assert.ok(plan.problems.some((problem) => problem.id === "mixed-time-behaviour"));
});

test("a break-down field the measures cannot be split by is set aside, and named", () => {
  const plan = buildExploreQuery(
    shapeOf({ measures: ["loans.narrow"], dimensions: ["grade", "branch"] }),
    CATALOGUE,
    TIME,
  );
  assert.deepEqual(plan.query?.dimensions, ["grade"]);
  assert.ok(
    plan.problems.some((problem) => problem.id === "dimension-not-sliceable"),
  );
});

test("the pivot field is never also a row field", () => {
  const plan = buildExploreQuery(
    shapeOf({
      measures: ["loans.balance"],
      dimensions: ["grade", "branch"],
      pivot: "branch",
    }),
    CATALOGUE,
    TIME,
  );
  assert.deepEqual(plan.query?.dimensions, ["grade"]);
  assert.equal(plan.query?.pivot?.dimension, "branch");
  assert.ok(
    plan.problems.some((problem) => problem.id === "pivot-was-a-row-field"),
  );
});

test("a pivot always asks for the server's own column cap", () => {
  const plan = buildExploreQuery(
    shapeOf({
      measures: ["loans.balance"],
      dimensions: ["grade"],
      pivot: "branch",
    }),
    CATALOGUE,
    TIME,
  );
  assert.equal(plan.query?.pivot?.maxColumns, BI_PIVOT_COLUMN_CAP);
});

test("a field wider than the column cap is refused before it is sent", () => {
  assert.ok(
    problemIds(
      shapeOf({
        measures: ["loans.balance"],
        dimensions: ["grade"],
        pivot: "wide",
      }),
    ).includes("pivot-too-wide"),
  );
});

test("a concentration measure is never pivoted", () => {
  const plan = buildExploreQuery(
    shapeOf({
      measures: ["loans.hhi"],
      dimensions: ["grade"],
      pivot: "branch",
    }),
    CATALOGUE,
    TIME,
  );
  assert.equal(plan.query, null);
  assert.ok(
    plan.problems.some((problem) => problem.id === "pivot-concentration"),
  );
});

test("a pivot with nothing in the rows is refused", () => {
  assert.ok(
    problemIds(
      shapeOf({ measures: ["loans.balance"], pivot: "branch" }),
    ).includes("pivot-needs-rows"),
  );
});

test("Top-N names one of the row fields, and never the pivot field", () => {
  const notARow = buildExploreQuery(
    shapeOf({
      measures: ["loans.balance"],
      dimensions: ["grade"],
      topN: { dimension: "branch", n: 5, other: true },
    }),
    CATALOGUE,
    TIME,
  );
  assert.equal(notARow.query?.topN, undefined);
  assert.ok(
    notARow.problems.some(
      (problem) => problem.id === "top-n-not-a-row-field",
    ),
  );

  const alsoThePivot = buildExploreQuery(
    shapeOf({
      measures: ["loans.balance"],
      dimensions: ["grade", "branch"],
      pivot: "branch",
      topN: { dimension: "branch", n: 5, other: true },
    }),
    CATALOGUE,
    TIME,
  );
  assert.equal(alsoThePivot.query?.topN, undefined);
});

test("a pivoted answer is never sorted by a measure", () => {
  const plan = buildExploreQuery(
    shapeOf({
      measures: ["loans.balance"],
      dimensions: ["grade"],
      pivot: "branch",
      sort: [
        { member: "loans.balance", direction: "desc" },
        { member: "grade", direction: "asc" },
      ],
    }),
    CATALOGUE,
    TIME,
  );
  assert.deepEqual(plan.query?.sort, [{ member: "grade", direction: "asc" }]);
  assert.ok(
    plan.problems.some(
      (problem) => problem.id === "sort-measure-under-pivot",
    ),
  );
});

test("a sort naming nothing in the question is dropped without comment", () => {
  const plan = buildExploreQuery(
    shapeOf({
      measures: ["loans.balance"],
      sort: [{ member: "grade", direction: "asc" }],
    }),
    CATALOGUE,
    TIME,
  );
  assert.deepEqual(plan.query?.sort, []);
});

test("sorts are capped at the server's maximum, and the reader is told", () => {
  const many = Array.from({ length: BI_SORT_CAP + 3 }, (_, index) => ({
    member: `d${index}`,
    direction: "asc" as const,
  }));
  const resolved = legalSorts(many, {
    measures: [],
    dimensions: many.map((entry) => entry.member),
    pivoted: false,
  });
  assert.equal(resolved.sort.length, BI_SORT_CAP);
  assert.equal(resolved.droppedOverCap, 3);
});

test("subtotals are dropped when there is nothing to roll up", () => {
  const plan = buildExploreQuery(
    shapeOf({ measures: ["loans.balance"], subtotals: true }),
    CATALOGUE,
    TIME,
  );
  assert.equal(plan.query?.subtotals, false);
});

test("too many filters is a refusal the reader can act on, not a 422", () => {
  const filters = Array.from({ length: BI_FILTER_CAP + 1 }, () => ({
    member: "grade",
    op: "eq" as const,
    values: ["c0"],
  }));
  const plan = buildExploreQuery(
    shapeOf({ measures: ["loans.balance"], filters }),
    CATALOGUE,
    TIME,
  );
  assert.equal(plan.query, null);
  assert.ok(plan.problems.some((problem) => problem.id === "filter-cap"));
});

test("a built query never carries its own paging, which the grid route forbids", () => {
  const plan = buildExploreQuery(
    shapeOf({ measures: ["loans.balance"], dimensions: ["grade"] }),
    CATALOGUE,
    TIME,
  );
  assert.ok(plan.query);
  assert.equal(plan.query!.limit, undefined);
  assert.equal(plan.query!.offset, undefined);
});

if (failures > 0) {
  console.error(`\n${failures} Explore query rule(s) failed.`);
  process.exit(1);
}
console.log(
  "exploreQuery.test.ts: the Explore controls cannot build a refused query.",
);
