import assert from "node:assert/strict";
import {
  hasEffectiveCapability,
  isHrefVisible,
  isPersonalSettingsPath,
  isPathVisible,
  hubRedirectFor,
  isRootPath,
  landingPathFor,
  type ModuleScope,
} from "./modules";

const resolved = (
  liquidityMonitoringAccess: boolean,
  capital: Partial<ModuleScope> = {},
): ModuleScope => ({
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
  capitalAggregatedView: true,
  capitalConfidentialView: true,
  capitalRestrictedView: true,
  capitalRun: true,
  ...capital,
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

const aggregatedCapitalOnly = resolved(true, {
  capitalConfidentialView: false,
  capitalRestrictedView: false,
  capitalRun: false,
});
assert.equal(isHrefVisible("/basel", aggregatedCapitalOnly), true);
assert.equal(isPathVisible("/basel/rwa", aggregatedCapitalOnly), true);
assert.equal(isPathVisible("/basel/structure", aggregatedCapitalOnly), true);
assert.equal(isPathVisible("/basel/stress", aggregatedCapitalOnly), true);
assert.equal(isHrefVisible("/basel/planning", aggregatedCapitalOnly), false);
assert.equal(isPathVisible("/basel/planning", aggregatedCapitalOnly), false);

const confidentialCapitalOnly = resolved(true, {
  capitalAggregatedView: false,
});
assert.equal(isHrefVisible("/basel", confidentialCapitalOnly), false);
assert.equal(isPathVisible("/basel", confidentialCapitalOnly), false);
assert.equal(isHrefVisible("/basel/planning", confidentialCapitalOnly), true);

const deniedCapital = resolved(true, {
  capitalAggregatedView: false,
  capitalConfidentialView: false,
});
assert.equal(isHrefVisible("/basel", deniedCapital), false);
assert.equal(isPathVisible("/basel/rwa", deniedCapital), false);
assert.equal(isPathVisible("/basel/planning", deniedCapital), false);

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
assert.equal(isPersonalSettingsPath("/settings/profile"), true);
assert.equal(isPersonalSettingsPath("/settings/profile/preferences"), true);
assert.equal(isPersonalSettingsPath("/settings"), false);
assert.equal(isPersonalSettingsPath("/settings/members"), false);

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

// Root landing: `/` is where every sign-in lands, so it resolves to the first
// visible surface rather than 404ing users without Command Center authority.
assert.equal(isRootPath("/"), true);
assert.equal(isRootPath("/?tour=1"), true);
assert.equal(isRootPath("//"), true);
assert.equal(isRootPath("/settings"), false);
assert.equal(landingPathFor(resolved(true)), "/");
assert.equal(landingPathFor(ownerOnly), "/settings");
assert.equal(landingPathFor(unresolved), null);

const liquidityOnly: ModuleScope = {
  ...resolved(true),
  modules: new Set(["liquidity"]),
};
assert.equal(isPathVisible("/", liquidityOnly), false);
assert.equal(landingPathFor(liquidityOnly), "/liquidity");

const capitalPlanningOnly: ModuleScope = {
  ...resolved(true, { capitalAggregatedView: false }),
  modules: new Set(["capital"]),
};
assert.equal(landingPathFor(capitalPlanningOnly), "/basel/planning");

const regulatoryOnly: ModuleScope = {
  ...resolved(true),
  modules: new Set(["regulatory_reporting", "reports"]),
  organizationModules: new Set(),
};
assert.equal(landingPathFor(regulatoryOnly), "/reports");

const nothingVisible: ModuleScope = {
  ...resolved(true),
  modules: new Set(),
  organizationModules: new Set(),
};
assert.equal(landingPathFor(nothingVisible), "/settings/profile");

// Hub redirects: only `/` and `/settings` redirect when hidden; deep links 404.
assert.equal(hubRedirectFor("/", ownerOnly), "/settings");
assert.equal(hubRedirectFor("/", resolved(true)), null);
assert.equal(hubRedirectFor("/", unresolved), null);
assert.equal(hubRedirectFor("/settings", operationalOnly), "/settings/profile");
assert.equal(hubRedirectFor("/settings/", operationalOnly), "/settings/profile");
assert.equal(hubRedirectFor("/settings/members", operationalOnly), null);
assert.equal(hubRedirectFor("/settings/authentication", operationalOnly), null);
assert.equal(hubRedirectFor("/liquidity/monitoring", denied), null);
assert.equal(hubRedirectFor("/fx", ownerOnly), null);

console.log(
  "modules.test.ts: binding-controlled navigation and deep links passed.",
);
