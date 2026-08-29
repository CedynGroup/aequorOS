import assert from "node:assert/strict";
import {
  canAddGrantToMember,
  grantAuthoritySentence,
  visibleGrantFragments,
} from "./grants";

const draft = {
  roleBundle: "analyst",
  institutionScope: "institution",
  institutionId: "BK-GH000001",
  institutionName: "Aequor Bank Ghana",
  moduleScope: "liq",
  sensitivityScope: "confidential",
  reason: "Treasury monitoring responsibilities",
} as const;

assert.equal(
  grantAuthoritySentence("Amma Owusu", draft),
  "Amma Owusu is an Analyst in Liquidity Monitoring for Aequor Bank Ghana, covering Confidential data.",
);
assert.equal(Array.isArray(draft.roleBundle), false);
assert.equal(Array.isArray(draft.institutionId), false);
assert.equal(Array.isArray(draft.moduleScope), false);
assert.equal(Array.isArray(draft.sensitivityScope), false);

const summary = visibleGrantFragments([
  {
    effective: true,
    roleBundle: "analyst",
    moduleScope: "liq",
    institutionScope: "institution",
    institutionName: "Aequor Bank Ghana",
  },
  {
    effective: true,
    roleBundle: "viewer",
    moduleScope: "cap",
    institutionScope: "institution",
    institutionName: "Aequor Bank Ghana",
  },
  {
    effective: true,
    roleBundle: "auditor",
    moduleScope: "audit",
    institutionScope: "organization",
  },
] as never);
assert.equal(summary.fragments.length, 2);
assert.equal(summary.remaining, 1);

assert.equal(
  canAddGrantToMember({
    authenticationMethod: "sso",
    lifecycleStatus: "deactivated",
    accessRequestState: "approval_needed",
  }),
  true,
);
assert.equal(
  canAddGrantToMember({
    authenticationMethod: "password",
    lifecycleStatus: "deactivated",
    accessRequestState: "none",
  }),
  false,
);
assert.equal(
  canAddGrantToMember({
    authenticationMethod: "service",
    lifecycleStatus: "active",
    accessRequestState: "none",
  }),
  false,
);

console.log("grants.test.ts: sentence and compact grant summaries passed.");
