import assert from "node:assert/strict";
import {
  hasEffectiveCapability,
  isHrefVisible,
  isPathVisible,
  type ModuleScope,
} from "./modules";

const resolved = (liquidityMonitoringAccess: boolean): ModuleScope => ({
  modules: new Set([
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
  ]),
  organizationModules: new Set(["settings"]),
  hasInstitutionAuthority: true,
  institutionClass: "bank",
  liquidityMonitoringAccess,
  isResolved: true,
});

const denied = resolved(false);
assert.equal(isHrefVisible("/liquidity/monitoring", denied), false);
assert.equal(isPathVisible("/liquidity/monitoring", denied), false);
assert.equal(isPathVisible("/liquidity/monitoring/detail", denied), false);
assert.equal(isHrefVisible("/liquidity", denied), true);
assert.equal(isPathVisible("/liquidity", denied), true);
assert.equal(isHrefVisible("/basel", denied), true);

const allowed = resolved(true);
assert.equal(isHrefVisible("/liquidity/monitoring", allowed), true);
assert.equal(isPathVisible("/liquidity/monitoring", allowed), true);
assert.equal(
  hasEffectiveCapability(
    [
      {
        module: "liq",
        sensitivity: "confidential",
        permission: "approve",
        requiresContextualAuthorization: true,
      },
    ],
    "liq",
    "confidential",
    "approve",
  ),
  false,
);

const ownerOnly: ModuleScope = {
  modules: new Set(),
  organizationModules: new Set(["settings"]),
  hasInstitutionAuthority: false,
  institutionClass: null,
  liquidityMonitoringAccess: false,
  isResolved: true,
};
assert.equal(isHrefVisible("/settings", ownerOnly), true);
assert.equal(isPathVisible("/settings/members", ownerOnly), true);
assert.equal(isHrefVisible("/", ownerOnly), false);
assert.equal(isHrefVisible("/liquidity", ownerOnly), false);
assert.equal(isPathVisible("/liquidity", ownerOnly), false);

const operationalOnly: ModuleScope = {
  ...resolved(true),
  organizationModules: new Set(),
};
assert.equal(isHrefVisible("/settings/profile", operationalOnly), true);
assert.equal(isPathVisible("/settings/profile", operationalOnly), true);
assert.equal(isHrefVisible("/settings", operationalOnly), false);
assert.equal(isPathVisible("/settings", operationalOnly), false);
assert.equal(isHrefVisible("/settings/members", operationalOnly), false);
assert.equal(isPathVisible("/settings/members", operationalOnly), false);
assert.equal(isHrefVisible("/settings/authentication", operationalOnly), false);
assert.equal(isPathVisible("/settings/authentication", operationalOnly), false);

const unresolved: ModuleScope = {
  modules: null,
  organizationModules: new Set(),
  hasInstitutionAuthority: false,
  institutionClass: null,
  liquidityMonitoringAccess: false,
  isResolved: false,
};
assert.equal(isHrefVisible("/liquidity/monitoring", unresolved), false);
assert.equal(isPathVisible("/liquidity/monitoring", unresolved), true);

console.log(
  "modules.test.ts: binding-controlled navigation and deep links passed.",
);
