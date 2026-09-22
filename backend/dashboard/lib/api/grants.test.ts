import assert from "node:assert/strict";
import type { ModuleScope } from "@aequoros/risk-service-api";
import {
  MODULE_OPTIONS,
  canAddGrantToMember,
  compactGrantFragment,
  visibleGrantFragments,
} from "./grants";

// The composer offers every module scope the generated contract accepts: a
// value added to the backend vocabulary without a label here would be
// ungrantable from Members. This is a compile-time check — the record below
// has a required key for each scope the options leave out.
type OfferedModuleScope = (typeof MODULE_OPTIONS)[number][0];
const everyModuleScopeIsOffered: Record<
  Exclude<ModuleScope, OfferedModuleScope>,
  never
> = {};
assert.deepEqual(everyModuleScopeIsOffered, {});
for (const [moduleScope, label] of [
  ["credit", "Credit"],
  ["institution", "Institution Profile"],
] as const) {
  assert.equal(
    compactGrantFragment({
      effective: true,
      roleBundle: "viewer",
      moduleScope,
      institutionScope: "institution",
      institutionName: "Aequor Bank Ghana",
    } as never),
    `Viewer · ${label} · Aequor Bank Ghana`,
  );
}

const draft = {
  roleBundle: "analyst",
  institutionScope: "institution",
  institutionId: "BK-GH000001",
  moduleScope: "liq",
  sensitivityScope: "confidential",
  reasonCategory: "other",
  reasonDetail: "Treasury monitoring responsibilities",
  reference: "",
  validUntil: "",
} as const;

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

console.log("grants.test.ts: scalar and compact grant summaries passed.");
