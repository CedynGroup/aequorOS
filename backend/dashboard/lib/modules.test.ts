import assert from "node:assert/strict";
import {
  hasEffectiveCapability,
  hrefAccess,
  isHrefVisible,
  isPersonalSettingsPath,
  isPathVisible,
  hubRedirectFor,
  isRootPath,
  landingPathFor,
  type ModuleScope,
} from "./modules";

const resolved = (
  liquidityAggregatedView: boolean,
  liquidityConfidentialView = liquidityAggregatedView,
  capabilities: Partial<ModuleScope> = {},
): ModuleScope => ({
  entitledModules: new Set([
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "fx",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
    "irrbb",
    "fx",
  ]),
  modules: new Set([
    "command_center",
    "risk",
    "alerts",
    "liquidity",
    "capital",
    "fx",
    "regulatory_reporting",
    "data_engine",
    "institution",
    "reports",
    "settings",
    "irrbb",
  ]),
  organizationModules: new Set(["settings"]),
  hasInstitutionAuthority: true,
  institutionClass: "bank",
  liquidityAggregatedView,
  liquidityConfidentialView,
  riskConfidentialView: true,
  capitalAggregatedView: true,
  capitalConfidentialView: true,
  capitalRestrictedView: true,
  capitalRun: true,
  irrbbAggregatedView: true,
  irrbbConfidentialView: true,
  irrbbRun: true,
  fxAggregatedView: true,
  fxConfidentialView: true,
  fxRun: true,
  ...capabilities,
  isResolved: true,
});

const denied = resolved(false, false);
assert.equal(isHrefVisible("/liquidity/monitoring", denied), false);
assert.equal(isPathVisible("/liquidity/monitoring", denied), false);
assert.equal(isPathVisible("/liquidity/monitoring/detail", denied), false);
assert.equal(isHrefVisible("/liquidity", denied), false);
assert.equal(isPathVisible("/liquidity", denied), false);
assert.equal(isHrefVisible("/basel", denied), true);
assert.deepEqual(hrefAccess("/liquidity/monitoring", denied), {
  state: "disabled",
  reason:
    "Requires Liquidity Monitoring · Confidential · View. Ask your organization owner or admin to grant it.",
});
assert.deepEqual(
  hrefAccess("/liquidity/stress", {
    ...denied,
    riskConfidentialView: false,
  }),
  {
    state: "disabled",
    reason:
      "Requires Liquidity Monitoring · Confidential · View and Risk & Limits · Confidential · View. Ask your organization owner or admin to grant them.",
  },
);
for (const moduleCase of [
  {
    prefix: "/irr",
    label: "IRRBB",
    aggregated: "irrbbAggregatedView",
    confidential: "irrbbConfidentialView",
    scenarioSensitivity: "Confidential",
  },
  {
    prefix: "/fx",
    label: "Foreign Exchange",
    aggregated: "fxAggregatedView",
    confidential: "fxConfidentialView",
    scenarioSensitivity: "Confidential",
  },
] as const) {
  const deniedModule = resolved(true, true, {
    [moduleCase.aggregated]: false,
    [moduleCase.confidential]: false,
  });
  for (const suffix of [
    "",
    "/?period=current",
    "/sensitivity/detail?period=current",
  ]) {
    const href = `${moduleCase.prefix}${suffix}`;
    assert.equal(isHrefVisible(href, deniedModule), false);
    assert.equal(isPathVisible(href, deniedModule), false);
    assert.deepEqual(hrefAccess(href, deniedModule), {
      state: "disabled",
      reason: `Requires ${moduleCase.label} · Aggregated · View. Ask your organization owner or admin to grant it.`,
    });
    assert.equal(isHrefVisible(href, resolved(true)), true);
    assert.equal(isPathVisible(href, resolved(true)), true);
  }
  for (const suffix of ["/scenarios", "/scenarios/detail?analysis=saved"]) {
    const href = `${moduleCase.prefix}${suffix}`;
    assert.equal(isPathVisible(href, deniedModule), false);
    assert.deepEqual(hrefAccess(href, deniedModule), {
      state: "disabled",
      reason: `Requires ${moduleCase.label} · ${moduleCase.scenarioSensitivity} · View. Ask your organization owner or admin to grant it.`,
    });
  }
  const confidentialOnlyModule = resolved(true, true, {
    [moduleCase.aggregated]: false,
    [moduleCase.confidential]: true,
  });
  assert.equal(isPathVisible(moduleCase.prefix, confidentialOnlyModule), false);
  assert.equal(
    isPathVisible(`${moduleCase.prefix}/scenarios`, confidentialOnlyModule),
    moduleCase.scenarioSensitivity === "Confidential",
  );
}

const aggregatedOnly = resolved(true, false);
assert.equal(isHrefVisible("/liquidity", aggregatedOnly), true);
assert.equal(isHrefVisible("/liquidity/buffer", aggregatedOnly), true);
assert.equal(isHrefVisible("/liquidity/stress", aggregatedOnly), false);
assert.equal(isHrefVisible("/liquidity/forecast", aggregatedOnly), false);
assert.equal(isHrefVisible("/liquidity/cfp", aggregatedOnly), false);
assert.deepEqual(hrefAccess("/liquidity/cfp", aggregatedOnly), {
  state: "disabled",
  reason:
    "Requires Liquidity Monitoring · Confidential · View. Ask your organization owner or admin to grant it.",
});

const confidentialOnly = resolved(false, true);
assert.equal(isHrefVisible("/liquidity", confidentialOnly), false);
assert.equal(isHrefVisible("/liquidity/monitoring", confidentialOnly), true);
assert.equal(isHrefVisible("/liquidity/cfp", confidentialOnly), true);

const allowed = resolved(true, true);
assert.equal(isHrefVisible("/liquidity/monitoring", allowed), true);
assert.equal(isPathVisible("/liquidity/monitoring", allowed), true);
assert.equal(isHrefVisible("/liquidity/stress", allowed), true);
const liquidityOnly = {
  ...allowed,
  modules: new Set(["liquidity"] as const),
  riskConfidentialView: false,
};
assert.equal(isHrefVisible("/liquidity/stress", liquidityOnly), false);
assert.deepEqual(hrefAccess("/liquidity/stress", liquidityOnly), {
  state: "disabled",
  reason:
    "Requires Risk & Limits · Confidential · View. Ask your organization owner or admin to grant it.",
});
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

const aggregatedCapitalOnly = resolved(true, true, {
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

const confidentialCapitalOnly = resolved(true, true, {
  capitalAggregatedView: false,
});
assert.equal(isHrefVisible("/basel", confidentialCapitalOnly), false);
assert.equal(isPathVisible("/basel", confidentialCapitalOnly), false);
assert.equal(isHrefVisible("/basel/planning", confidentialCapitalOnly), true);

const deniedCapital = resolved(true, true, {
  capitalAggregatedView: false,
  capitalConfidentialView: false,
});
assert.equal(isHrefVisible("/basel", deniedCapital), false);
assert.equal(isPathVisible("/basel/rwa", deniedCapital), false);
assert.equal(isPathVisible("/basel/planning", deniedCapital), false);

const aggregatedFxOnly = resolved(true, true, {
  fxConfidentialView: false,
  fxRun: false,
});
assert.equal(isHrefVisible("/fx", aggregatedFxOnly), true);
assert.equal(isPathVisible("/fx/var", aggregatedFxOnly), true);
assert.deepEqual(hrefAccess("/fx/scenarios", aggregatedFxOnly), {
  state: "disabled",
  reason:
    "Requires Foreign Exchange · Confidential · View. Ask your organization owner or admin to grant it.",
});

const deniedFx = resolved(true, true, {
  fxAggregatedView: false,
  fxConfidentialView: true,
});
assert.equal(isHrefVisible("/fx", deniedFx), false);
assert.deepEqual(hrefAccess("/fx", deniedFx), {
  state: "disabled",
  reason:
    "Requires Foreign Exchange · Aggregated · View. Ask your organization owner or admin to grant it.",
});
assert.equal(isPathVisible("/fx/scenarios", deniedFx), true);

const ownerOnly: ModuleScope = {
  modules: new Set(),
  organizationModules: new Set(["settings"]),
  hasInstitutionAuthority: false,
  institutionClass: null,
  liquidityAggregatedView: false,
  liquidityConfidentialView: false,
  irrbbAggregatedView: false,
  irrbbConfidentialView: false,
  irrbbRun: false,
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
  liquidityAggregatedView: false,
  liquidityConfidentialView: false,
  irrbbAggregatedView: false,
  irrbbConfidentialView: false,
  irrbbRun: false,
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

assert.equal(isPathVisible("/", liquidityOnly), false);
assert.equal(landingPathFor(liquidityOnly), "/liquidity");

const capitalPlanningOnly: ModuleScope = {
  ...resolved(true, true, { capitalAggregatedView: false }),
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
assert.equal(
  hubRedirectFor("/settings/", operationalOnly),
  "/settings/profile",
);
assert.equal(hubRedirectFor("/settings/members", operationalOnly), null);
assert.equal(hubRedirectFor("/settings/authentication", operationalOnly), null);
assert.equal(hubRedirectFor("/liquidity/monitoring", denied), null);
assert.equal(hubRedirectFor("/fx", ownerOnly), null);
const structurallyExcluded = {
  ...denied,
  entitledModules: new Set(["capital"] as const),
};
assert.deepEqual(hrefAccess("/liquidity", structurallyExcluded), {
  state: "hidden",
});

console.log(
  "modules.test.ts: binding-controlled navigation and deep links passed.",
);
