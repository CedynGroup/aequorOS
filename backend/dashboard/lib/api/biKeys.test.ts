/**
 * Proof that two different BI scopes cannot share a cache entry.
 *
 * The server re-decides every BI request, so a browser cache cannot hand out
 * data the server would refuse to FETCH. What it can do is hand out data the
 * server already fetched for someone else, or for another institution, or under
 * an authorization generation that has since been revoked — because a cache hit
 * answers without asking. Every dimension below is therefore checked twice:
 * that changing it changes the key, and that two keys differing only in it are
 * not mutually matching TanStack prefixes.
 *
 * Run: pnpm --filter @aequoros/dashboard test
 */

import assert from "node:assert/strict";
import { queryAuthorityScope } from "./queryPolicy";
import {
  BI_QUERY_PREFIX,
  biCatalogueKey,
  biDashboardKey,
  biDashboardSharesKey,
  biDashboardVersionsKey,
  biDashboardsKey,
  biDrillKey,
  biExplainKey,
  biGridKey,
  biAskKey,
  BI_ASK_PREFIX,
  biMeasureKey,
  biMeasuresKey,
  biQueryFingerprint,
  biQueryKey,
  biTrustKey,
  biWindowKey,
  canonicalJson,
  isoDay,
  utcDay,
} from "./biKeys";

const TENANT_A = "OR-DEM00001";
const TENANT_B = "OR-OTHER001";
const BANK_A = "BK-SAMP0001";
const BANK_B = "BK-SAMP0002";
const DASHBOARD_A = "3f6b1d1e-4c5a-4f2b-9e7d-0a1b2c3d4e5f";
const DASHBOARD_B = "7c2a8b90-1d3e-4a5b-8c6d-9e0f1a2b3c4d";
const MEASURE_A = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d";
const MEASURE_B = "9f8e7d6c-5b4a-4938-8271-6f5e4d3c2b1a";
const QUESTION_A = "6f2a1c90-1d3e-4a5b-8c6d-9e0f1a2b3c4d";
const QUESTION_B = "b1d4e7f0-2a3b-4c5d-8e9f-0a1b2c3d4e5f";
const WORDS_A = "gross loans by branch";
const WORDS_B = "deposits by product";

const analyst = queryAuthorityScope(TENANT_A, "analyst@aequoros.example", 7);
const otherTenantAnalyst = queryAuthorityScope(
  TENANT_B,
  "analyst@aequoros.example",
  7,
);
const otherUser = queryAuthorityScope(TENANT_A, "examiner@aequoros.example", 7);
const regranted = queryAuthorityScope(TENANT_A, "analyst@aequoros.example", 8);

const nplQuery = {
  measures: ["loans.npl_ratio_pct"],
  dimensions: ["loans.sector"],
  filters: [],
  time: { asOf: utcDay("2026-06-30") },
};

const depositQuery = {
  measures: ["deposits.balance_rc"],
  dimensions: ["loans.sector"],
  filters: [],
  time: { asOf: utcDay("2026-06-30") },
};

const filteredQuery = {
  ...nplQuery,
  filters: [{ member: "loans.sector", op: "eq", values: ["agriculture"] }],
};

const key = (value: readonly unknown[]): string => JSON.stringify(value);

/** TanStack matches a key against a PREFIX, so containment is the real test. */
function isPrefixOf(prefix: readonly unknown[], candidate: readonly unknown[]) {
  if (prefix.length > candidate.length) return false;
  return prefix.every(
    (part, index) => JSON.stringify(part) === JSON.stringify(candidate[index]),
  );
}

function assertDistinct(
  dimension: string,
  left: readonly unknown[],
  right: readonly unknown[],
): void {
  assert.notEqual(
    key(left),
    key(right),
    `${dimension} must change the cache key`,
  );
  assert.equal(
    isPrefixOf(left, right),
    false,
    `${dimension}: one key must not be a prefix of the other`,
  );
  assert.equal(
    isPrefixOf(right, left),
    false,
    `${dimension}: one key must not be a prefix of the other`,
  );
}

// --- the five dimensions, one at a time -------------------------------------

const base = biQueryKey(analyst, BANK_A, nplQuery) as readonly unknown[];

assertDistinct(
  "tenant",
  base,
  biQueryKey(otherTenantAnalyst, BANK_A, nplQuery) as readonly unknown[],
);
assertDistinct(
  "signed-in user",
  base,
  biQueryKey(otherUser, BANK_A, nplQuery) as readonly unknown[],
);
assertDistinct(
  "authorization generation",
  base,
  biQueryKey(regranted, BANK_A, nplQuery) as readonly unknown[],
);
assertDistinct(
  "institution",
  base,
  biQueryKey(analyst, BANK_B, nplQuery) as readonly unknown[],
);
assertDistinct(
  "as-of date",
  base,
  biQueryKey(analyst, BANK_A, {
    ...nplQuery,
    time: { asOf: utcDay("2026-03-31") },
  }) as readonly unknown[],
);
assertDistinct(
  "measure",
  base,
  biQueryKey(analyst, BANK_A, depositQuery) as readonly unknown[],
);
assertDistinct(
  "filter",
  base,
  biQueryKey(analyst, BANK_A, filteredQuery) as readonly unknown[],
);
assertDistinct(
  "comparison date",
  base,
  biQueryKey(analyst, BANK_A, {
    ...nplQuery,
    time: { asOf: utcDay("2026-06-30"), compareTo: utcDay("2026-03-31") },
  }) as readonly unknown[],
);

// An institution that has not resolved yet is its own dimension, never a match
// for the first bank that does resolve.
assertDistinct(
  "pending institution",
  biQueryKey(analyst, null, nplQuery) as readonly unknown[],
  base,
);

// --- the same question is the same key --------------------------------------

assert.equal(
  key(biQueryKey(analyst, BANK_A, nplQuery) as readonly unknown[]),
  key(
    biQueryKey(analyst, BANK_A, {
      time: { asOf: utcDay("2026-06-30") },
      filters: [],
      dimensions: ["loans.sector"],
      measures: ["loans.npl_ratio_pct"],
    }) as readonly unknown[],
  ),
  "property order must not fork the cache",
);

assert.equal(
  key(biQueryKey(analyst, BANK_A, nplQuery) as readonly unknown[]),
  key(
    biQueryKey(analyst, BANK_A, {
      ...nplQuery,
      limit: undefined,
    }) as readonly unknown[],
  ),
  "an omitted optional must not fork the cache",
);

// --- the query dimension is reversible, so it cannot collide ----------------

assert.notEqual(
  biQueryFingerprint(nplQuery),
  biQueryFingerprint(depositQuery),
  "two different questions must not share a fingerprint",
);
assert.equal(
  JSON.parse(biQueryFingerprint({ b: 2, a: [1, "x"], c: null })).a[1],
  "x",
  "the fingerprint must stay reversible — a digest could collide",
);
assert.equal(
  canonicalJson({ b: 1, a: 2 }),
  '{"a":2,"b":1}',
  "object keys must be sorted",
);
assert.equal(
  canonicalJson([{ b: 1, a: 2 }]),
  '[{"a":2,"b":1}]',
  "arrays keep their order; their members are canonicalised",
);
assert.equal(
  canonicalJson(Number.NaN),
  "null",
  "a non-finite number must serialise deterministically",
);

// --- every surface carries the same dimensions ------------------------------

for (const [surface, left, right] of [
  [
    "catalogue",
    biCatalogueKey(analyst, BANK_A),
    biCatalogueKey(analyst, BANK_B),
  ],
  [
    "trust",
    biTrustKey(analyst, BANK_A, "2026-06-30"),
    biTrustKey(analyst, BANK_A, "2026-03-31"),
  ],
  [
    "grid",
    biGridKey(analyst, BANK_A, { query: nplQuery, startRow: 0, endRow: 100 }),
    biGridKey(analyst, BANK_A, { query: nplQuery, startRow: 100, endRow: 200 }),
  ],
  [
    "drill",
    biDrillKey(analyst, BANK_A, { query: nplQuery, startRow: 0 }),
    biDrillKey(analyst, BANK_B, { query: nplQuery, startRow: 0 }),
  ],
  [
    "explain",
    biExplainKey(analyst, BANK_A, "loans.npl_ratio_pct", nplQuery),
    biExplainKey(analyst, BANK_A, "loans.balance_rc", nplQuery),
  ],
  // SAVED DASHBOARDS. The list is the set of documents this identity may open and
  // one resolved document is the widget set this identity was granted, so both are
  // decided per principal and per institution: a key that dropped either would
  // hand one colleague the other's answer. The history and the share list carry no
  // figure and are keyed identically — the share list names the PEOPLE a document
  // reaches and is the owner's alone.
  [
    "saved dashboards",
    biDashboardsKey(analyst, BANK_A),
    biDashboardsKey(analyst, BANK_B),
  ],
  [
    "one saved dashboard",
    biDashboardKey(analyst, BANK_A, DASHBOARD_A, "2026-06-30"),
    biDashboardKey(analyst, BANK_A, DASHBOARD_A, "2026-03-31"),
  ],
  [
    "another saved dashboard",
    biDashboardKey(analyst, BANK_A, DASHBOARD_A, "2026-06-30"),
    biDashboardKey(analyst, BANK_A, DASHBOARD_B, "2026-06-30"),
  ],
  [
    "dashboard history",
    biDashboardVersionsKey(analyst, BANK_A, DASHBOARD_A),
    biDashboardVersionsKey(analyst, BANK_A, DASHBOARD_B),
  ],
  [
    "dashboard shares",
    biDashboardSharesKey(analyst, BANK_A, DASHBOARD_A),
    biDashboardSharesKey(analyst, BANK_A, DASHBOARD_B),
  ],
  // CALCULATED MEASURES. A measure row is a FORMULA, not a figure — and the list
  // is still decided per principal: `content.readable_measures` serves this
  // identity's own drafts plus the institution's proposals and certified
  // measures, and then drops every one whose figures this identity's access does
  // not cover. A refused measure is ABSENT, so a cache hit across principals
  // would hand one colleague a formula the server would not have named to them.
  // There is no window dimension: a formula is not read at a date.
  [
    "calculated measures",
    biMeasuresKey(analyst, BANK_A),
    biMeasuresKey(analyst, BANK_B),
  ],
  [
    "one calculated measure",
    biMeasureKey(analyst, BANK_A, MEASURE_A),
    biMeasureKey(analyst, BANK_A, MEASURE_B),
  ],
  [
    "the measure list and one measure are different questions",
    biMeasuresKey(analyst, BANK_A),
    biMeasureKey(analyst, BANK_A, MEASURE_A),
  ],
] as const) {
  assertDistinct(
    surface,
    left as readonly unknown[],
    right as readonly unknown[],
  );
}

// The measure surface, on every dimension in turn — not only the institution the
// loop above varies. A formula names catalogue figures, and which figures an
// identity may be shown is the same decision the query path makes, so a key that
// dropped the actor or the authorization generation would serve a revoked reader
// a list the server would no longer produce.
for (const [dimension, other] of [
  ["tenant", otherTenantAnalyst],
  ["signed-in user", otherUser],
  ["authorization generation", regranted],
] as const) {
  assertDistinct(
    `calculated measures: ${dimension}`,
    biMeasuresKey(analyst, BANK_A) as readonly unknown[],
    biMeasuresKey(other, BANK_A) as readonly unknown[],
  );
  assertDistinct(
    `one calculated measure: ${dimension}`,
    biMeasureKey(analyst, BANK_A, MEASURE_A) as readonly unknown[],
    biMeasureKey(other, BANK_A, MEASURE_A) as readonly unknown[],
  );
}

for (const surfaceKey of [
  biCatalogueKey(analyst, BANK_A),
  biTrustKey(analyst, BANK_A, "2026-06-30"),
  biQueryKey(analyst, BANK_A, nplQuery),
  biGridKey(analyst, BANK_A, { query: nplQuery }),
  biDrillKey(analyst, BANK_A, { query: nplQuery }),
  biExplainKey(analyst, BANK_A, "loans.npl_ratio_pct", nplQuery),
  biDashboardsKey(analyst, BANK_A),
  biDashboardKey(analyst, BANK_A, DASHBOARD_A, "2026-06-30"),
  biDashboardVersionsKey(analyst, BANK_A, DASHBOARD_A),
  biDashboardSharesKey(analyst, BANK_A, DASHBOARD_A),
  biMeasuresKey(analyst, BANK_A),
  biMeasureKey(analyst, BANK_A, MEASURE_A),
  biAskKey(analyst, BANK_A, QUESTION_A, WORDS_A, "2026-06-30"),
] as readonly (readonly unknown[])[]) {
  assert.equal(
    surfaceKey[1],
    TENANT_A,
    "every BI key must carry the tenant in position 1",
  );
  assert.equal(
    surfaceKey[2],
    analyst.authorityId,
    "every BI key must carry the actor and authorization generation in position 2",
  );
  assert.equal(
    surfaceKey[3],
    BANK_A,
    "every BI key must carry the institution in position 3",
  );
}

// Surfaces never share a prefix with one another, so invalidating one cannot
// silently resurrect another's rows.
assert.equal(
  (biQueryKey(analyst, BANK_A, nplQuery) as readonly unknown[])[0],
  BI_QUERY_PREFIX,
);
assert.equal(
  isPrefixOf(
    biQueryKey(analyst, BANK_A, nplQuery) as readonly unknown[],
    biGridKey(analyst, BANK_A, { query: nplQuery }) as readonly unknown[],
  ),
  false,
);

// --- one natural-language question ------------------------------------------
//
// The question is the fifth dimension, and it is a dimension in its own right:
// two readers may ask the same words and be proposed different queries, and the
// same reader may ask two different things a second apart. The route 404s for
// anybody but the principal who asked, so a cache hit that crossed principals
// would answer WITHOUT asking the server — which is the one thing this module
// exists to prevent.

const askBase = biAskKey(
  analyst,
  BANK_A,
  QUESTION_A,
  WORDS_A,
  "2026-06-30",
) as readonly unknown[];

assert.equal(askBase[0], BI_ASK_PREFIX);
assertDistinct(
  "ask: tenant",
  askBase,
  biAskKey(
    otherTenantAnalyst,
    BANK_A,
    QUESTION_A,
    WORDS_A,
    "2026-06-30",
  ) as readonly unknown[],
);
assertDistinct(
  "ask: signed-in user",
  askBase,
  biAskKey(
    otherUser,
    BANK_A,
    QUESTION_A,
    WORDS_A,
    "2026-06-30",
  ) as readonly unknown[],
);
assertDistinct(
  "ask: authorization generation",
  askBase,
  biAskKey(
    regranted,
    BANK_A,
    QUESTION_A,
    WORDS_A,
    "2026-06-30",
  ) as readonly unknown[],
);
assertDistinct(
  "ask: institution",
  askBase,
  biAskKey(
    analyst,
    BANK_B,
    QUESTION_A,
    WORDS_A,
    "2026-06-30",
  ) as readonly unknown[],
);
assertDistinct(
  "ask: reporting date",
  askBase,
  biAskKey(
    analyst,
    BANK_A,
    QUESTION_A,
    WORDS_A,
    "2026-03-31",
  ) as readonly unknown[],
);
assertDistinct(
  "ask: the question itself",
  askBase,
  biAskKey(
    analyst,
    BANK_A,
    QUESTION_A,
    WORDS_B,
    "2026-06-30",
  ) as readonly unknown[],
);
assertDistinct(
  "ask: which question",
  askBase,
  biAskKey(
    analyst,
    BANK_A,
    QUESTION_B,
    WORDS_A,
    "2026-06-30",
  ) as readonly unknown[],
);
// A key built before the question had an id must never match one built after.
assertDistinct(
  "ask: an id that has not arrived yet",
  askBase,
  biAskKey(analyst, BANK_A, null, WORDS_A, "2026-06-30") as readonly unknown[],
);
// Two different questions never share an entry, whichever way they differ.
assert.notEqual(
  key(
    biAskKey(
      analyst,
      BANK_A,
      QUESTION_A,
      WORDS_A,
      "2026-06-30",
    ) as readonly unknown[],
  ),
  key(
    biAskKey(
      analyst,
      BANK_A,
      QUESTION_B,
      WORDS_B,
      "2026-06-30",
    ) as readonly unknown[],
  ),
);
// And the ask surface shares a prefix with no other BI surface.
for (const otherSurface of [
  biQueryKey(analyst, BANK_A, nplQuery),
  biCatalogueKey(analyst, BANK_A),
  biMeasuresKey(analyst, BANK_A),
] as readonly (readonly unknown[])[]) {
  assert.equal(isPrefixOf(askBase, otherSurface), false);
  assert.equal(isPrefixOf(otherSurface, askBase), false);
}

// --- date normalisation ------------------------------------------------------

assert.equal(isoDay("2026-06-30T00:00:00Z"), "2026-06-30");
assert.equal(isoDay(utcDay("2026-06-30")), "2026-06-30");
assert.equal(isoDay(null), null);
assert.equal(isoDay(new Date("not a date")), null);
assert.equal(biWindowKey({}), "unset");
assert.equal(
  biWindowKey({ start: "2026-01-01", end: "2026-06-30" }),
  "2026-01-01..2026-06-30",
);
assert.notEqual(
  biWindowKey({ asOf: "2026-06-30" }),
  biWindowKey({ asOf: "2026-06-30", compareTo: "2026-03-31" }),
);

console.log("biKeys.test.ts: BI cache keys are scoped on every dimension.");
