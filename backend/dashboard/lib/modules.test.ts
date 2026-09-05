import assert from "node:assert/strict";
import { isHrefVisible, isPathVisible, type ModuleScope } from "./modules";

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

const unresolved: ModuleScope = {
  modules: null,
  institutionClass: null,
  liquidityMonitoringAccess: false,
  isResolved: false,
};
assert.equal(isHrefVisible("/liquidity/monitoring", unresolved), false);
assert.equal(isPathVisible("/liquidity/monitoring", unresolved), true);

console.log(
  "modules.test.ts: binding-controlled navigation and deep links passed.",
);
