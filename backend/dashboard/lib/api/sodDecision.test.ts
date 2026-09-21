/**
 * A refused grant must say which rule refused it.
 *
 * The live case: an Org Owner granting themselves Validator is blocked by C9,
 * correctly — and was shown only "The scoped grant conflicts with
 * separation-of-duties policy", which names no rule and no remedy. The Owner's
 * natural next move is to re-compose the grant with different scopes, which
 * can never work: C9 is about who the identity is, not how narrow the grant is.
 *
 * Run by `pnpm --filter @aequoros/dashboard test`.
 */

import assert from "node:assert/strict";

import { sodFindings, sodRemedy } from "./sodDecision";

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

const c9 = {
  code: "c9_account_administration_operational_conflict",
  message:
    "Account administration and operational maker/checker authority must remain separated for one identity.",
};

test("the server's findings are read off the refusal", () => {
  const found = sodFindings({ sod_decision: { outcome: "block", findings: [c9] } });
  assert.equal(found.length, 1);
  assert.equal(found[0].code, c9.code);
  assert.match(found[0].message, /must remain separated/);
});

test("C9 gets a remedy that does not say 'narrow the scope'", () => {
  const remedy = sodRemedy([c9]);
  assert.ok(remedy);
  assert.match(remedy, /different person|revoke/i);
  // The wrong advice would be to re-scope; this conflict is not scope-sensitive.
  assert.ok(!/narrow/i.test(remedy), remedy);
});

test("an unknown rule gets no invented advice", () => {
  assert.equal(sodRemedy([{ code: "something_new", message: "x" }]), null);
  assert.equal(sodRemedy([]), null);
});

test("a malformed payload yields nothing rather than a blank bullet", () => {
  for (const details of [
    null,
    undefined,
    "x",
    42,
    [],
    {},
    { sod_decision: null },
    { sod_decision: { findings: "nope" } },
    { sod_decision: { findings: [null, 3, {}, { code: "a" }] } },
  ]) {
    assert.deepEqual(sodFindings(details), []);
  }
});

test("a finding without a code still shows its message", () => {
  const found = sodFindings({ sod_decision: { findings: [{ message: "why" }] } });
  assert.equal(found.length, 1);
  assert.equal(found[0].code, "");
  assert.equal(found[0].message, "why");
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("sodDecision: all tests passed");
