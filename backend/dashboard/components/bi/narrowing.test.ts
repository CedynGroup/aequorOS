/**
 * A pack's tiles cannot be narrowed by a field their figures do not carry.
 *
 * `app/services/bi/compiler.py` treats a FILTER as a slice: it checks every
 * filter's dimension against each measure's `allowed_dimensions` and refuses
 * with `<measure> cannot be sliced by <dimension>`. An engine-copied figure —
 * net interest margin, a capital ratio — is one institution-level number the
 * platform copies from the engine rather than recomputing, so it allows no
 * position dimension at all.
 *
 * The filter bar offers every dimension in the reader's CATALOGUE, and the
 * canvas used to apply the page's filters to EVERY widget. So narrowing an ALCO
 * pack by one field broke every engine-metric tile on it at once, with the raw
 * member ids shown to the reader. That is the regression these cases pin.
 *
 * The rule is deliberately NOT re-implemented here or in `query.ts`: both defer
 * to `exploreQuery.ts::sliceableDimensions`, which Explore has used since it
 * shipped. Two copies of "which fields may slice this measure" is how the pack
 * canvas and Explore came to disagree in the first place.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";

import type {
  BiCatalogueRead,
  BiFilter,
  BiQuery,
} from "@aequoros/risk-service-api";

import { applicableNarrowing, narrowingFieldsFor } from "./query";

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

/** The two dimensions and two measures the cases below need, and nothing else. */
const CATALOGUE = {
  measures: [
    {
      id: "position.balance_rc",
      label: "Balance",
      allowedDimensions: ["position.product_family", "position.branch"],
    },
    {
      // The founder's case: an engine copy, sliceable by nothing.
      id: "engine.portfolio_nim_pct.advisory_internal.official",
      label: "Net interest margin",
      allowedDimensions: [],
    },
  ],
  // Both carry a CLOSED vocabulary: the bar only ever offers fields whose values
  // are published, because free-text entry on an aggregate is a probe.
  dimensions: [
    {
      id: "position.product_family",
      label: "Product family",
      values: [{ code: "retail_loans", label: "Retail loans" }],
    },
    {
      id: "position.branch",
      label: "Branch",
      values: [{ code: "BR-001", label: "Head office" }],
    },
  ],
} as unknown as BiCatalogueRead;

function queryFor(...measures: string[]): BiQuery {
  return { measures, time: { asOf: "2026-08-31" } } as unknown as BiQuery;
}

const PRODUCT_FAMILY: BiFilter = {
  member: "position.product_family",
  op: "eq",
  values: ["retail_loans"],
} as unknown as BiFilter;

const BRANCH: BiFilter = {
  member: "position.branch",
  op: "eq",
  values: ["BR-001"],
} as unknown as BiFilter;

test("a measure that allows the field is narrowed by it", () => {
  const { query, withheld } = applicableNarrowing(
    queryFor("position.balance_rc"),
    [PRODUCT_FAMILY],
    CATALOGUE,
  );
  assert.deepEqual(query.filters, [PRODUCT_FAMILY]);
  assert.deepEqual(withheld, []);
});

test("an engine copy is NOT narrowed, and the field is named in its label", () => {
  const { query, withheld } = applicableNarrowing(
    queryFor("engine.portfolio_nim_pct.advisory_internal.official"),
    [PRODUCT_FAMILY],
    CATALOGUE,
  );
  // The query still goes — an institution-wide margin is a real answer — but it
  // goes WITHOUT the filter, so the compiler has nothing to refuse.
  assert.deepEqual(query.filters, []);
  // The reader is told in words, never by member id.
  assert.deepEqual(withheld, ["Product family"]);
});

test("a widget mixing both measures is narrowed by neither", () => {
  // `sliceableDimensions` requires EVERY measure to allow the field, which is
  // what the compiler checks: one unsliceable measure refuses the whole query.
  const { query, withheld } = applicableNarrowing(
    queryFor(
      "position.balance_rc",
      "engine.portfolio_nim_pct.advisory_internal.official",
    ),
    [PRODUCT_FAMILY],
    CATALOGUE,
  );
  assert.deepEqual(query.filters, []);
  assert.deepEqual(withheld, ["Product family"]);
});

test("filters are judged one at a time, not all or nothing", () => {
  const allowsOnlyProduct = {
    ...CATALOGUE,
    measures: [
      {
        id: "position.balance_rc",
        label: "Balance",
        allowedDimensions: ["position.product_family"],
      },
    ],
  } as unknown as BiCatalogueRead;
  const { query, withheld } = applicableNarrowing(
    queryFor("position.balance_rc"),
    [PRODUCT_FAMILY, BRANCH],
    allowsOnlyProduct,
  );
  assert.deepEqual(query.filters, [PRODUCT_FAMILY]);
  assert.deepEqual(withheld, ["Branch"]);
});

test("a measure the catalogue does not describe withholds rather than gambles", () => {
  const { query, withheld } = applicableNarrowing(
    queryFor("position.something_unpublished"),
    [PRODUCT_FAMILY],
    CATALOGUE,
  );
  assert.deepEqual(query.filters, []);
  assert.deepEqual(withheld, ["Product family"]);
});

test("no filters means no catalogue lookup and nothing withheld", () => {
  const { query, withheld } = applicableNarrowing(
    queryFor("engine.portfolio_nim_pct.advisory_internal.official"),
    [],
    undefined,
  );
  assert.deepEqual(query.filters, []);
  assert.deepEqual(withheld, []);
});

test("a widget's own authored filter survives a withheld page filter", () => {
  // The pack authored its own slice; the page could not add to it. The authored
  // one must remain, or the tile would silently widen to the whole book.
  const authored = {
    ...queryFor("engine.portfolio_nim_pct.advisory_internal.official"),
    filters: [BRANCH],
  } as unknown as BiQuery;
  const { query, withheld } = applicableNarrowing(
    authored,
    [PRODUCT_FAMILY],
    CATALOGUE,
  );
  assert.deepEqual(query.filters, [BRANCH]);
  assert.deepEqual(withheld, ["Product family"]);
});

// --- what the bar may OFFER, as opposed to what a tile may carry -------------

test("a dashboard offers only fields at least one of its widgets can carry", () => {
  // An ALCO-shaped page: one position widget, one engine widget. Product family
  // reaches the first; nothing reaches the second. Branch reaches neither,
  // because no measure here allows it.
  const offered = narrowingFieldsFor(CATALOGUE, [
    ["position.balance_rc"],
    ["engine.portfolio_nim_pct.advisory_internal.official"],
  ]);
  assert.deepEqual(
    offered.map((dimension) => dimension.id),
    ["position.product_family", "position.branch"],
  );
});

test("a field no widget on the page can carry is not offered at all", () => {
  const offered = narrowingFieldsFor(CATALOGUE, [
    ["engine.portfolio_nim_pct.advisory_internal.official"],
  ]);
  // This is the founder's complaint: the bar used to offer the whole catalogue,
  // so a page of engine copies advertised fields that broke every tile on it.
  assert.deepEqual(offered, []);
});

test("with no widgets named, the whole catalogue is offered (Explore)", () => {
  const offered = narrowingFieldsFor(CATALOGUE, []);
  assert.deepEqual(offered.map((dimension) => dimension.id), [
    "position.product_family",
    "position.branch",
  ]);
});

if (failures > 0) {
  console.error(`\n${failures} narrowing rule(s) failed.`);
  process.exit(1);
}
console.log(
  "narrowing.test.ts: a pack tile is never narrowed by a field its figures do not carry.",
);
