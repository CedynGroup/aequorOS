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
  biDrillKey,
  biExplainKey,
  biGridKey,
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
] as const) {
  assertDistinct(
    surface,
    left as readonly unknown[],
    right as readonly unknown[],
  );
}

for (const surfaceKey of [
  biCatalogueKey(analyst, BANK_A),
  biTrustKey(analyst, BANK_A, "2026-06-30"),
  biQueryKey(analyst, BANK_A, nplQuery),
  biGridKey(analyst, BANK_A, { query: nplQuery }),
  biDrillKey(analyst, BANK_A, { query: nplQuery }),
  biExplainKey(analyst, BANK_A, "loans.npl_ratio_pct", nplQuery),
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
