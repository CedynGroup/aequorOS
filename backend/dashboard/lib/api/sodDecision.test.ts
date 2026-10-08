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

import { conflictGrantActions, sodFindings, sodRemedy } from "./sodDecision";

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
  const found = sodFindings({
    sod_decision: { outcome: "block", findings: [c9] },
  });
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
  const found = sodFindings({
    sod_decision: { findings: [{ message: "why" }] },
  });
  assert.equal(found.length, 1);
  assert.equal(found[0].code, "");
  assert.equal(found[0].message, "why");
});

test("the conflicting grant ids are read off a refusal", () => {
  const found = sodFindings({
    sod_decision: {
      findings: [{ ...c9, conflicting_binding_ids: ["g-1", 7, "g-2"] }],
    },
  });
  assert.deepEqual(found[0].conflictingBindingIds, ["g-1", "g-2"]);
});

const approverGrant = {
  id: "g-approver",
  roleBundle: "approver",
  effective: true,
};
const ownerGrant = { id: "g-owner", roleBundle: "org_owner", effective: true };
const revokedGrant = { id: "g-old", roleBundle: "approver", effective: false };
const finding = (ids: string[]) => ({
  code: "approval_and_transmission_separation_required",
  message: "x",
  conflictingBindingIds: ids,
});

test("a grant administrator gets the conflicting grant to review, once", () => {
  const actions = conflictGrantActions(
    [finding(["g-approver"]), finding(["g-approver"])],
    [approverGrant, ownerGrant],
    true,
  );
  assert.deepEqual(actions.reviewable, [approverGrant]);
  assert.equal(actions.askAdministrator, false);
});

test("a missing conflict retains the administrator fallback for every viewer", () => {
  for (const canAdminister of [true, false]) {
    const actions = conflictGrantActions(
      [finding(["g-approver", "missing"])],
      [approverGrant],
      canAdminister,
    );
    assert.deepEqual(actions.reviewable, canAdminister ? [approverGrant] : []);
    assert.equal(actions.askAdministrator, true);
  }
});

test("anyone else is told to ask an account administrator, with no link", () => {
  const actions = conflictGrantActions(
    [finding(["g-approver"])],
    [approverGrant],
    false,
  );
  assert.deepEqual(actions.reviewable, []);
  assert.equal(actions.askAdministrator, true);
});

test("ownership and inactive grants offer nothing to change", () => {
  for (const canAdminister of [true, false]) {
    const actions = conflictGrantActions(
      [finding(["g-owner", "g-old"])],
      [ownerGrant, revokedGrant],
      canAdminister,
    );
    assert.deepEqual(actions.reviewable, []);
    assert.equal(actions.askAdministrator, false);
  }
});

if (failures > 0) {
  console.error(`${failures} test(s) failed`);
  process.exit(1);
}
console.log("sodDecision: all tests passed");
