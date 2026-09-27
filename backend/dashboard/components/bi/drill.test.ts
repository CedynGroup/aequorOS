/**
 * The drill-through mapping's one property: a destination is offered ONLY when
 * it can reproduce the figure's slice exactly.
 *
 * Every case below is a way the mapping could quietly widen the reader's view —
 * a dropped field, a multi-value filter flattened to one, an absent group, a
 * substring search passed off as an exact filter, a date left behind. A link
 * that lands on a wider book is worse than no link, because the reader believes
 * they are looking at the figure's own rows.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import type {
  BiFilter,
  BiQuery,
  BiResultColumn,
} from "@aequoros/risk-service-api";
import { drillDestinations, sliceForRow } from "./drill";

function dimension(id: string): BiResultColumn {
  return {
    id,
    label: id,
    kind: "dimension",
    format: "text",
    memberId: id,
  } as BiResultColumn;
}

function measure(id: string): BiResultColumn {
  return {
    id,
    label: id,
    kind: "measure",
    format: "amount",
    memberId: id,
  } as BiResultColumn;
}

function query(overrides: Partial<BiQuery> = {}): BiQuery {
  return {
    measures: ["loans.balance_rc"],
    dimensions: [],
    filters: [],
    time: { asOf: new Date("2026-06-30T00:00:00.000Z") },
    ...overrides,
  } as BiQuery;
}

function href(destinations: readonly { href: string }[]): string {
  assert.equal(
    destinations.length,
    1,
    `expected exactly one destination, got ${destinations.length}`,
  );
  return destinations[0].href;
}

// --- a loan slice reaches the loan book, on the figure's own date ------------

{
  const result = {
    columns: [dimension("loan.sector"), measure("loans.balance_rc")],
  };
  const destinations = drillDestinations(
    result,
    ["Agriculture", "12500000"],
    query({ dimensions: ["loan.sector"] }),
  );
  assert.deepEqual(
    destinations.map((destination) => destination.id),
    ["loan_book"],
  );
  const url = new URL(href(destinations), "https://bank.example");
  assert.equal(url.pathname, "/credit/book");
  assert.equal(url.searchParams.get("sector"), "Agriculture");
  assert.equal(
    url.searchParams.get("as_of"),
    "2026-06-30",
    "the destination must land on the date the figure was measured on, not on today's book",
  );
}

// The widget's own filters are part of the slice, not just the row's group.
{
  const result = {
    columns: [dimension("loan.dpd_band"), measure("loans.balance_rc")],
  };
  const url = new URL(
    href(
      drillDestinations(
        result,
        ["90_179", "4000000"],
        query({
          dimensions: ["loan.dpd_band"],
          filters: [
            { member: "loan.grade", op: "eq", values: ["substandard"] },
          ] as BiFilter[],
        }),
      ),
    ),
    "https://bank.example",
  );
  assert.equal(url.searchParams.get("dpd_band"), "90_179");
  assert.equal(
    url.searchParams.get("grade"),
    "substandard",
    "a filter the page narrowed by must travel with the drill, or the destination shows a wider book",
  );
}

// A numeric member (the IFRS 9 stage) survives as its own value.
{
  const result = {
    columns: [dimension("loan.ifrs9_stage"), measure("loans.balance_rc")],
  };
  const url = new URL(
    href(
      drillDestinations(
        result,
        [2, "900000"],
        query({ dimensions: ["loan.ifrs9_stage"] }),
      ),
    ),
    "https://bank.example",
  );
  assert.equal(url.searchParams.get("stage"), "2");
}

// --- every way a slice must withhold the destination ------------------------

{
  // A field with no exact filter on the destination. The loan book has no
  // currency filter, so "loans in USD" has nowhere honest to go.
  const result = {
    columns: [dimension("position.currency"), measure("loans.balance_rc")],
  };
  assert.deepEqual(
    drillDestinations(
      result,
      ["USD", "1200000"],
      query({ dimensions: ["position.currency"] }),
    ),
    [],
    "a field the destination cannot filter on must withhold the destination, never be dropped",
  );
}

{
  // The "not stated" group. No destination has an is-absent filter.
  const result = {
    columns: [dimension("loan.sector"), measure("loans.balance_rc")],
  };
  assert.deepEqual(
    drillDestinations(
      result,
      [null, "500000"],
      query({ dimensions: ["loan.sector"] }),
    ),
    [],
    "a group with no value must not become an unfiltered page",
  );
}

{
  // A multi-value `in` is not one value, and a negation excludes.
  const result = {
    columns: [dimension("loan.sector"), measure("loans.balance_rc")],
  };
  for (const filter of [
    { member: "loan.grade", op: "in", values: ["substandard", "doubtful"] },
    { member: "loan.grade", op: "ne", values: ["standard"] },
    { member: "loan.grade", op: "not_in", values: ["standard"] },
    { member: "loan.dpd_band", op: "gte", values: ["90_179"] },
  ] as BiFilter[]) {
    assert.deepEqual(
      drillDestinations(
        result,
        ["Agriculture", "500000"],
        query({ dimensions: ["loan.sector"], filters: [filter] }),
      ),
      [],
      `a ${filter.op} filter cannot be carried as one value and must withhold the destination`,
    );
  }
}

{
  // A slice that pins nothing at all is the whole book, not this figure's rows.
  const result = { columns: [measure("loans.balance_rc")] };
  assert.deepEqual(drillDestinations(result, ["84850000"], query()), []);
}

// --- a position slice reaches the blotter, and a reference pins one row ------

{
  const result = {
    columns: [dimension("position.type"), measure("positions.balance_rc")],
  };
  const url = new URL(
    href(
      drillDestinations(
        result,
        ["DEPOSIT", "80570000"],
        query({
          measures: ["positions.balance_rc"],
          dimensions: ["position.type"],
        }),
      ),
    ),
    "https://bank.example",
  );
  assert.equal(url.pathname, "/positions");
  assert.equal(url.searchParams.get("type"), "DEPOSIT");
  assert.equal(url.searchParams.get("as_of"), "2026-06-30");
}

{
  // A reference goes to the blotter as `ref` — the exact row, with lineage —
  // and never as the substring search, which would match LOAN/10 for LOAN/1.
  const result = {
    columns: [
      dimension("position.source_reference"),
      measure("positions.balance_rc"),
    ],
  };
  const url = new URL(
    href(
      drillDestinations(
        result,
        ["LOAN/1", "30000000"],
        query({
          measures: ["positions.balance_rc"],
          dimensions: ["position.source_reference"],
        }),
      ),
    ),
    "https://bank.example",
  );
  assert.equal(url.searchParams.get("ref"), "LOAN/1");
  assert.equal(url.searchParams.get("q"), null);
}

{
  // The destination follows the FIGURE. A deposits-by-branch table pins a
  // branch the loan book would filter on, and a page of loans is not an answer
  // about deposits — so the loan book is not offered, and the branch is not a
  // position-blotter filter either, so nothing is.
  const result = {
    columns: [dimension("branch.code"), measure("deposits.balance_rc")],
  };
  assert.deepEqual(
    drillDestinations(
      result,
      ["BR-001", "25000000"],
      query({
        measures: ["deposits.balance_rc"],
        dimensions: ["branch.code"],
      }),
    ),
    [],
  );
}

// --- the slice itself -------------------------------------------------------

{
  const result = {
    columns: [dimension("loan.sector"), measure("loans.balance_rc")],
  };
  const slice = sliceForRow(
    result,
    ["Agriculture", "1"],
    query({
      dimensions: ["loan.sector"],
      // The row's group is at least as narrow as a filter on the same field.
      filters: [{ member: "loan.sector", op: "eq", values: ["Trade"] }],
    }),
  );
  assert.deepEqual(slice.members, [
    { member: "loan.sector", value: "Agriculture" },
  ]);
  assert.equal(slice.exact, true);
  assert.equal(slice.asOf, "2026-06-30");
}

{
  // No date on the query means no date on the link: the destination shows its
  // own current book and says so, rather than being sent a date nobody chose.
  const result = {
    columns: [dimension("loan.sector"), measure("loans.balance_rc")],
  };
  const url = new URL(
    href(
      drillDestinations(
        result,
        ["Trade", "1"],
        query({ dimensions: ["loan.sector"], time: {} }),
      ),
    ),
    "https://bank.example",
  );
  assert.equal(url.searchParams.get("as_of"), null);
  assert.equal(url.searchParams.get("sector"), "Trade");
}

console.log(
  "drill.test.ts: a drill destination is offered only where it reproduces the figure's slice exactly.",
);
