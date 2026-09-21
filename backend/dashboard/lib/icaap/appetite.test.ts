/**
 * Node tests for the client mirror of the appetite ordering rule.
 *
 * What is pinned here is not arithmetic, it is the three ways this mirror could
 * mislead a preparer: by getting a direction backwards, by inventing a
 * comparison against a value that does not exist, or by claiming a half-filled
 * form is wrong.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import {
  checkAppetiteOrdering,
  parseLevels,
  scaleDomain,
  scaleFraction,
} from "./appetite";

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

// ---------------------------------------------------------------------------
// Direction
// ---------------------------------------------------------------------------

test("higher-is-safer: appetite above tolerance above capacity is valid", () => {
  const result = checkAppetiteOrdering("higher_is_safer", {
    appetite: "16",
    tolerance: "14",
    capacity: "13",
    reference: "13",
  });
  assert.deepEqual(result.violations, []);
  assert.equal(result.referenceAssessed, true);
});

test("higher-is-safer: the same numbers the other way round are violations", () => {
  const result = checkAppetiteOrdering("higher_is_safer", {
    appetite: "13",
    tolerance: "14",
    capacity: "16",
  });
  const codes = result.violations.map((v) => v.code);
  assert.deepEqual(codes, [
    "appetite_beyond_tolerance",
    "tolerance_beyond_capacity",
  ]);
});

test("lower-is-safer: the ordering runs the other way", () => {
  const ok = checkAppetiteOrdering("lower_is_safer", {
    appetite: "3",
    tolerance: "5",
    capacity: "8",
    reference: "10",
  });
  assert.deepEqual(ok.violations, []);

  const bad = checkAppetiteOrdering("lower_is_safer", {
    appetite: "8",
    tolerance: "5",
    capacity: "3",
  });
  assert.deepEqual(
    bad.violations.map((v) => v.code),
    ["appetite_beyond_tolerance", "tolerance_beyond_capacity"],
  );
});

test("equality is allowed in both directions", () => {
  for (const direction of ["higher_is_safer", "lower_is_safer"] as const) {
    const result = checkAppetiteOrdering(direction, {
      appetite: "10",
      tolerance: "10",
      capacity: "10",
      reference: "10",
    });
    assert.deepEqual(result.violations, [], direction);
  }
});

// ---------------------------------------------------------------------------
// The regulatory reference (D-036)
// ---------------------------------------------------------------------------

test("an absent regulatory reference is reported, never assumed", () => {
  const result = checkAppetiteOrdering("higher_is_safer", {
    appetite: "16",
    tolerance: "14",
    capacity: "13",
    reference: null,
  });
  assert.equal(result.referenceAssessed, false);
  assert.deepEqual(result.violations, []);
});

test("an empty-string reference is absence, not zero", () => {
  const result = checkAppetiteOrdering("higher_is_safer", {
    capacity: "13",
    reference: "",
  });
  assert.equal(result.referenceAssessed, false);
  // The dangerous alternative: reading "" as 0 makes every capacity clear the
  // floor, which is exactly the fail-open shape this platform has been bitten by.
  assert.deepEqual(result.violations, []);
  assert.equal(parseLevels({ reference: "" }).reference, null);
});

test("a capacity beyond the governed reference is a violation", () => {
  const higher = checkAppetiteOrdering("higher_is_safer", {
    capacity: "12",
    reference: "13",
  });
  assert.deepEqual(
    higher.violations.map((v) => v.code),
    ["capacity_beyond_regulatory_reference"],
  );

  const lower = checkAppetiteOrdering("lower_is_safer", {
    capacity: "12",
    reference: "10",
  });
  assert.deepEqual(
    lower.violations.map((v) => v.code),
    ["capacity_beyond_regulatory_reference"],
  );
});

// ---------------------------------------------------------------------------
// Partial input
// ---------------------------------------------------------------------------

test("a half-filled form produces no violation", () => {
  for (const partial of [
    { appetite: "16" },
    { tolerance: "14" },
    { capacity: "13" },
    { appetite: "16", capacity: "13" },
    {},
  ]) {
    const result = checkAppetiteOrdering("higher_is_safer", partial);
    assert.deepEqual(
      result.violations,
      [],
      `not-entered-yet is not wrong: ${JSON.stringify(partial)}`,
    );
  }
});

test("a pair is only compared when both of its values are present", () => {
  // appetite vs tolerance is skipped, capacity vs reference still fires.
  const result = checkAppetiteOrdering("higher_is_safer", {
    appetite: "16",
    capacity: "12",
    reference: "13",
  });
  assert.deepEqual(
    result.violations.map((v) => v.code),
    ["capacity_beyond_regulatory_reference"],
  );
});

test("every violation carries a sentence, not a code", () => {
  const result = checkAppetiteOrdering("higher_is_safer", {
    appetite: "10",
    tolerance: "14",
  });
  for (const violation of result.violations) {
    assert.ok(violation.message.length > violation.code.length);
    assert.ok(
      !violation.message.includes("_"),
      "a violation message must not leak the raw code",
    );
  }
});

// ---------------------------------------------------------------------------
// Scale geometry
// ---------------------------------------------------------------------------

test("no scale is drawn when there is nothing to span", () => {
  assert.equal(scaleDomain([]), null);
  assert.equal(scaleDomain([null, null]), null);
  assert.equal(scaleDomain([5]), null);
  assert.equal(scaleDomain([5, 5]), null);
});

test("the safe side is the right-hand side, whichever way the numbers run", () => {
  const domain = scaleDomain([10, 20]);
  assert.ok(domain);
  const safer = scaleFraction(20, domain, "higher_is_safer");
  const riskier = scaleFraction(10, domain, "higher_is_safer");
  assert.ok(safer > riskier, "a higher value must sit further right");

  const saferLow = scaleFraction(10, domain, "lower_is_safer");
  const riskierHigh = scaleFraction(20, domain, "lower_is_safer");
  assert.ok(saferLow > riskierHigh, "a lower value must sit further right");
});

test("a fraction never leaves the drawn track", () => {
  const domain = scaleDomain([10, 20]);
  assert.ok(domain);
  for (const value of [-1000, 0, 15, 1000]) {
    for (const direction of ["higher_is_safer", "lower_is_safer"] as const) {
      const fraction = scaleFraction(value, domain, direction);
      assert.ok(fraction >= 0 && fraction <= 1, `${value} ${direction}`);
    }
  }
});

if (failures > 0) {
  console.error(`${failures} appetite mirror test(s) failed`);
  process.exit(1);
}
console.log("appetite ordering mirror: all checks passed");
