import assert from "node:assert/strict";
import test from "node:test";
import { GROUP_ROLES, SYNTHETIC_STAFF, rolesForGroups } from "../../lib/auth/staff";
import { ROLES } from "../../lib/api/types";
import { ROLE_LABEL, roleLabel } from "../../lib/labels";
import { capabilities, isReadOnly, roleTitle, submissionActions } from "../../lib/roles";

test("identity groups map to the five roles; unknown groups grant nothing", () => {
  assert.deepEqual(rolesForGroups(["srg-supervisors"]), ["supervisor"]);
  assert.deepEqual(rolesForGroups(["srg-returns-policy", "srg-access-approvers", "srg-returns-policy"]), ["returns_policy", "access_approver"]);
  assert.deepEqual(rolesForGroups(["srg-onboarding", "everyone"]), [], "the onboarding role no longer exists");
  assert.deepEqual(Object.values(GROUP_ROLES).sort(), [...ROLES].sort());
});

test("the synthetic staff are SPEC Part 4's", () => {
  const byName = Object.fromEntries(SYNTHETIC_STAFF.map((s) => [s.name, s.groups]));
  assert.deepEqual(byName, {
    "Kofi Owusu": ["srg-senior-supervisors"],
    "Akosua Boateng": ["srg-supervisors"],
    "Esi Asante": ["srg-returns-policy"],
    "Michael Adjei": ["srg-returns-policy", "srg-access-approvers"],
    "Internal Audit": ["srg-internal-audit"],
  });
});

test("capabilities follow ARCHITECTURE.md's role table", () => {
  assert.equal(capabilities.supervise(["supervisor"]), true);
  assert.equal(capabilities.supervise(["senior_supervisor"]), true);
  assert.equal(capabilities.supervise(["auditor"]), false);
  assert.equal(capabilities.supervise(["returns_policy"]), false);
  assert.equal(capabilities.decideReturns(["returns_policy"]), true);
  assert.equal(capabilities.decideReturns(["senior_supervisor"]), false);
  assert.equal(capabilities.approveAccess(["access_approver"]), true);
  assert.equal(capabilities.approveAccess(["senior_supervisor", "returns_policy"]), false);
  assert.equal(capabilities.exportAudit(["auditor"]), true);
  assert.equal(capabilities.verifyAudit([]), false);
  assert.equal("onboard" in capabilities, false, "no enrolment capability exists");
});

test("auditors are read-only; anyone with a deciding role is not", () => {
  assert.equal(isReadOnly(["auditor"]), true);
  assert.equal(isReadOnly([]), true);
  assert.equal(isReadOnly(["auditor", "supervisor"]), false);
});

test("submission buttons come only from allowed_actions", () => {
  assert.deepEqual(submissionActions([]), { accept: false, query: false, resubmission: false, assign: false, any: false });
  assert.deepEqual(submissionActions(["accept", "request_resubmission"]), { accept: true, query: false, resubmission: true, assign: false, any: true });
});

test("role titles and labels are words, never enum values", () => {
  assert.equal(roleTitle(["senior_supervisor"]), "Senior Supervisor");
  assert.equal(roleTitle(["access_approver", "returns_policy"]), "Returns Policy Officer · Access Approver");
  assert.equal(roleTitle([]), "No role assigned");
  for (const role of ROLES) assert.doesNotMatch(roleLabel(role), /_/);
  assert.equal(Object.keys(ROLE_LABEL).length, ROLES.length);
});
