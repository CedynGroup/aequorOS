import assert from "node:assert/strict";
import {
  effectiveInstitutionModules,
  effectiveOrganizationModules,
  forecastingWorkspaceAccess,
  hasEffectiveCapability,
  hrefAccess,
  isAskPath,
  isHrefVisible,
  isPersonalSettingsPath,
  isPathVisible,
  hubRedirectFor,
  moduleForPath,
  isRootPath,
  landingPathFor,
  type ModuleKey,
  type ModuleScope,
  type ModuleKey as ModuleKeyForTest,
} from "./modules";
import { existsSync as fileExists, readFileSync } from "node:fs";
import { join as joinPath, resolve as resolvePath } from "node:path";
import { ICAAP_CYCLE_TABS, icaapTabHrefs } from "../components/icaap/tabs";

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
    "ftp",
    "forecasting",
    "behavioral",
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
    "ftp",
    "forecasting",
    "behavioral",
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
  ftpAggregatedView: true,
  ftpConfidentialView: true,
  ftpRun: true,
  forecastingAggregatedView: true,
  forecastingConfidentialView: true,
  forecastingRun: true,
  behavioralAggregatedView: true,
  behavioralRun: true,
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
    confidentialRoute: "/scenarios",
    scenarioSensitivity: "Confidential",
  },
  {
    prefix: "/fx",
    label: "Foreign Exchange",
    aggregated: "fxAggregatedView",
    confidential: "fxConfidentialView",
    confidentialRoute: "/scenarios",
    scenarioSensitivity: "Confidential",
  },
  {
    prefix: "/ftp",
    label: "Funds Transfer Pricing",
    aggregated: "ftpAggregatedView",
    confidential: "ftpConfidentialView",
    confidentialRoute: "/scenarios",
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
  for (const suffix of [
    moduleCase.confidentialRoute,
    `${moduleCase.confidentialRoute}/detail?analysis=saved`,
  ]) {
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
    isPathVisible(
      `${moduleCase.prefix}${moduleCase.confidentialRoute}`,
      confidentialOnlyModule,
    ),
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
        dataScope: {
          kind: "all",
          branches: [] as string[],
          regions: [] as string[],
        },
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

const aggregatedFtpOnly = resolved(true, true, {
  ftpConfidentialView: false,
  ftpRun: false,
});
assert.equal(isHrefVisible("/ftp", aggregatedFtpOnly), true);
assert.equal(isPathVisible("/ftp/products", aggregatedFtpOnly), true);
assert.deepEqual(hrefAccess("/ftp/scenarios", aggregatedFtpOnly), {
  state: "disabled",
  reason:
    "Requires Funds Transfer Pricing · Confidential · View. Ask your organization owner or admin to grant it.",
});

const deniedFtp = resolved(true, true, {
  ftpAggregatedView: false,
  ftpConfidentialView: true,
});
assert.equal(isHrefVisible("/ftp", deniedFtp), false);
assert.deepEqual(hrefAccess("/ftp", deniedFtp), {
  state: "disabled",
  reason:
    "Requires Funds Transfer Pricing · Aggregated · View. Ask your organization owner or admin to grant it.",
});
assert.equal(isPathVisible("/ftp/scenarios", deniedFtp), true);

const aggregatedForecastingOnly = resolved(true, true, {
  forecastingConfidentialView: false,
  forecastingRun: false,
});
assert.equal(isHrefVisible("/forecasting", aggregatedForecastingOnly), true);
assert.equal(
  isPathVisible("/forecasting/assumptions", aggregatedForecastingOnly),
  true,
);
for (const href of [
  "/forecasting/nii",
  "/forecasting/whatif",
  "/forecasting/reverse-stress",
  "/forecasting/optimizer",
]) {
  assert.deepEqual(hrefAccess(href, aggregatedForecastingOnly), {
    state: "disabled",
    reason:
      "Requires Forecasting · Confidential · View. Ask an Org Owner to grant access via Settings → Members.",
  });
}

const deniedForecasting = resolved(true, true, {
  forecastingAggregatedView: false,
  forecastingConfidentialView: true,
});
assert.equal(isHrefVisible("/forecasting", deniedForecasting), false);
assert.deepEqual(hrefAccess("/forecasting", deniedForecasting), {
  state: "disabled",
  reason:
    "Requires Forecasting · Aggregated · View. Ask an Org Owner to grant access via Settings → Members.",
});
assert.equal(isPathVisible("/forecasting/scenario", deniedForecasting), true);
// Behavioral has no confidential page: every tab needs the same aggregated
// view, and a run-only analyst is still shown the exact sentence they lack.
const deniedBehavioral = resolved(true, true, {
  behavioralAggregatedView: false,
  behavioralRun: true,
});
for (const href of [
  "/behavioral",
  "/behavioral/nmd-duration",
  "/behavioral/prepayment?period=current",
  "/behavioral/deposit-stability",
  "/behavioral/liquidity",
]) {
  assert.equal(isHrefVisible(href, deniedBehavioral), false);
  assert.equal(isPathVisible(href, deniedBehavioral), false);
  assert.deepEqual(hrefAccess(href, deniedBehavioral), {
    state: "disabled",
    reason:
      "Requires Behavioral Models · Aggregated · View. Ask your organization owner or admin to grant it.",
  });
  assert.equal(isHrefVisible(href, resolved(true)), true);
  assert.equal(isPathVisible(href, resolved(true)), true);
}
const behavioralReader = resolved(true, true, { behavioralRun: false });
assert.equal(isHrefVisible("/behavioral/nmd-duration", behavioralReader), true);
assert.equal(isPathVisible("/behavioral/liquidity", behavioralReader), true);

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

const memberOnly: ModuleScope = {
  modules: new Set(),
  organizationModules: new Set(),
  hasInstitutionAuthority: false,
  institutionClass: null,
  liquidityAggregatedView: false,
  liquidityConfidentialView: false,
  isResolved: true,
};
assert.equal(landingPathFor(memberOnly), "/");
assert.equal(isPathVisible("/", memberOnly), true);
assert.deepEqual(hrefAccess("/liquidity", memberOnly), {
  state: "disabled",
  reason:
    "Requires Liquidity Monitoring · Aggregated · View. Ask your organization owner or admin to grant it.",
});
assert.deepEqual(hrefAccess("/basel", memberOnly), {
  state: "disabled",
  reason:
    "Requires Basel Capital · Aggregated · View. Ask your organization owner or admin to grant it.",
});
assert.equal(hrefAccess("/settings", memberOnly).state, "enabled");
for (const path of ["/irr", "/irr/sensitivity/detail"]) {
  assert.deepEqual(hrefAccess(path, memberOnly), {
    state: "disabled",
    reason:
      "Requires IRRBB · Aggregated · View. Ask your organization owner or admin to grant it.",
  });
  assert.equal(isHrefVisible(path, memberOnly), false);
  assert.equal(isPathVisible(path, memberOnly), false);
}
assert.deepEqual(hrefAccess("/irr/scenarios", memberOnly), {
  state: "disabled",
  reason:
    "Requires IRRBB · Confidential · View. Ask your organization owner or admin to grant it.",
});
assert.equal(isHrefVisible("/irr/scenarios", memberOnly), false);
assert.equal(isPathVisible("/irr/scenarios", memberOnly), false);
assert.equal(hubRedirectFor("/irr/scenarios", memberOnly), "/");

// ---------------------------------------------------------------------------
// The IRRBB standardised framework (P5)
//
// It reads on AGGREGATED view like the rest of the module — the CONFIDENTIAL
// authority it needs is for MINTING a run, which the screen gates on its own.
// Putting the page behind confidential view would hide a supervisory-monitoring
// view from the analyst who has to read it.
//
// It is hidden for an SDI: the whole framework parameter set is governed for
// the bank institution class, so an SDI has no governed value for any of it and
// every run would refuse. The nav must not offer a walled-up door.
// ---------------------------------------------------------------------------

assert.ok(
  fileExists(
    joinPath(
      __dirname.includes(".test-out")
        ? resolvePath(__dirname, "../..")
        : resolvePath(__dirname, ".."),
      "app",
      "(app)",
      "irr",
      "standardised",
      "page.tsx",
    ),
  ),
  "the standardised framework tab is routed but has no page.tsx behind it",
);
assert.equal(isHrefVisible("/irr/standardised", resolved(true, true)), true);
assert.equal(isPathVisible("/irr/standardised", resolved(true, true)), true);
assert.deepEqual(hrefAccess("/irr/standardised", memberOnly), {
  state: "disabled",
  reason:
    "Requires IRRBB · Aggregated · View. Ask your organization owner or admin to grant it.",
});
assert.equal(isHrefVisible("/irr/standardised", memberOnly), false);
assert.equal(hubRedirectFor("/irr/standardised", memberOnly), "/");
assert.equal(
  isHrefVisible("/irr/standardised", {
    ...resolved(true, true),
    institutionClass: "sdi",
  }),
  false,
);
assert.deepEqual(
  hrefAccess("/irr/standardised", {
    ...resolved(true, true),
    institutionClass: "sdi",
  }),
  { state: "hidden" },
);
// The other IRRBB tabs are unchanged by that exclusion.
assert.equal(
  isHrefVisible("/irr/sensitivity", {
    ...resolved(true, true),
    institutionClass: "sdi",
  }),
  true,
);

// Hubs redirect when hidden; a member without institution authority also
// returns from public product-module paths to the root empty workspace.
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
assert.equal(hubRedirectFor("/liquidity/monitoring", memberOnly), "/");
for (const route of [
  "/data-engine",
  "/data-engine/excel-csv",
  "/liquidity/stress/",
]) {
  assert.equal(hubRedirectFor(route, memberOnly), "/");
}
for (const route of [
  "/data-engine/batches/00000000-0000-0000-0000-000000000000",
  "/data-engine/batches/foreign-batch",
  "/data-engine/batches",
  "/data-engine/unknown",
  "/liquidity/monitoring/detail",
  "/liquidity/stress/unknown",
  "/does-not-exist",
]) {
  assert.equal(hubRedirectFor(route, memberOnly), null);
}
assert.equal(
  hubRedirectFor("/liquidity/buffer", {
    ...memberOnly,
    institutionClass: "sdi",
  }),
  null,
);
assert.equal(
  hubRedirectFor("/fx", {
    ...memberOnly,
    entitledModules: new Set(["liquidity"]),
  }),
  null,
);
for (const route of [
  "/liquidity/forecast",
  "/liquidity/monitoring",
  "/liquidity/cfp",
]) {
  assert.deepEqual(hrefAccess(route, memberOnly), {
    state: "disabled",
    reason:
      "Requires Liquidity Monitoring · Confidential · View. Ask your organization owner or admin to grant it.",
  });
}
assert.deepEqual(hrefAccess("/liquidity/stress", memberOnly), {
  state: "disabled",
  reason:
    "Requires Liquidity Monitoring · Confidential · View and Risk & Limits · Confidential · View. Ask your organization owner or admin to grant them.",
});
assert.deepEqual(hrefAccess("/basel/planning", memberOnly), {
  state: "hidden",
});
assert.deepEqual(hrefAccess("/data-engine/batches/foreign-batch", memberOnly), {
  state: "hidden",
});
const structurallyExcluded = {
  ...denied,
  entitledModules: new Set(["capital"] as const),
};
assert.deepEqual(hrefAccess("/liquidity", structurallyExcluded), {
  state: "hidden",
});

// Account view is projected at organization scope, never institution scope.
const accountView = {
  module: "account",
  sensitivity: "restricted",
  permission: "view",
  requiresContextualAuthorization: false,
  dataScope: { kind: "all", branches: [] as string[], regions: [] as string[] },
} as const;
for (const [capabilities, visible] of [
  [[accountView], true],
  [[], false],
  [[{ ...accountView, permission: "administer" }], false],
  [[{ ...accountView, sensitivity: "aggregated" }], false],
  [
    [
      {
        ...accountView,
        requiresContextualAuthorization: true,
        dataScope: {
          kind: "all",
          branches: [] as string[],
          regions: [] as string[],
        },
      },
    ],
    false,
  ],
] as const) {
  const scope = resolved(true, true, {
    modules: effectiveInstitutionModules(null, [accountView]),
    organizationModules: effectiveOrganizationModules(capabilities),
  });
  for (const route of [
    "/institution",
    "/institution/parties",
    "/institution/registers",
  ]) {
    assert.equal(isPathVisible(route, scope), visible);
    assert.equal(isHrefVisible(route, scope), visible);
  }
}

// ---------------------------------------------------------------------------
// Credit is its own module (2026-09-22,
// backend/docs/credit_enforcement_rollout.md). A CREDIT/aggregated view admits
// it on its own. A `risk` view STILL admits it: the mirror migration copies
// every active human `risk` row to `credit`, and a tenant that has not migrated
// holds only the `risk` row — dropping it would hide the module from today's
// users for nothing. With neither, the module is hidden; the hub link then
// names the Credit grant, which is the one an Org Owner would issue afresh.
// ---------------------------------------------------------------------------

const creditView = {
  module: "credit",
  sensitivity: "aggregated",
  permission: "view",
  requiresContextualAuthorization: false,
  dataScope: { kind: "all", branches: [] as string[], regions: [] as string[] },
} as const;
const riskView = {
  ...creditView,
  module: "risk",
  sensitivity: "confidential",
} as const;
const liquidityView = { ...creditView, module: "liq" } as const;
// Credit joined every institution type's default set (migration 202609010046),
// so a real bank payload entitles it; the shared fixture above predates that.
const creditEntitled = new Set([
  "command_center",
  "risk",
  "alerts",
  "liquidity",
  "capital",
  "credit",
  "regulatory_reporting",
  "data_engine",
  "institution",
  "reports",
  "settings",
] as const);
const CREDIT_ROUTES = ["/credit", "/credit/book", "/credit/concentration"];

for (const [capabilities, visible] of [
  [[creditView], true],
  [[riskView], true],
  [[creditView, riskView], true],
  [[liquidityView], false],
  [[], false],
  // Only a VIEW admits a module; a run grant without one does not.
  [[{ ...creditView, permission: "run" }], false],
] as const) {
  const modules = effectiveInstitutionModules(null, capabilities);
  assert.equal(modules.has("credit"), visible);
  const scope = resolved(true, true, {
    modules,
    entitledModules: creditEntitled,
    hasInstitutionAuthority: capabilities.length > 0,
  });
  for (const route of CREDIT_ROUTES) {
    assert.equal(isPathVisible(route, scope), visible);
    assert.equal(isHrefVisible(route, scope), visible);
  }
}

// The institution type's entitlement still bounds both paths in: a credit or
// mirrored risk view cannot surface a module the tenant's class does not carry.
assert.equal(
  effectiveInstitutionModules(["liquidity"], [creditView]).has("credit"),
  false,
);
assert.equal(
  effectiveInstitutionModules(["liquidity"], [riskView]).has("credit"),
  false,
);
assert.equal(
  effectiveInstitutionModules(["credit"], [riskView]).has("credit"),
  true,
);
assert.equal(
  effectiveInstitutionModules(["credit"], [creditView]).has("credit"),
  true,
);
// A credit view is a credit view only: it does not drag the Risk & Limits
// surfaces in with it the way the mirrored `risk` row does. The property is
// pinned by NAME as well as by the exact set, so that a module legitimately
// joining the set later (as `bi` did) cannot quietly take the risk surfaces
// with it.
const fromCreditView = effectiveInstitutionModules(null, [creditView]);
for (const dragged of ["risk", "alerts", "positions", "command_center"]) {
  assert.equal(
    fromCreditView.has(dragged as ModuleKeyForTest),
    false,
    `a credit view must not admit ${dragged}`,
  );
}
// `bi` is in the set because Business Intelligence is the READING surface over
// whatever a principal already holds: a credit view opens the credit measures
// of the catalogue, and nothing else. It is not an institution-type module and
// has no entitlement of its own.
assert.deepEqual([...fromCreditView].sort(), ["bi", "credit"]);

// A member with no authority at all is told the grant that opens the hub, and
// deep links below it stay hidden rather than disabled.
assert.deepEqual(hrefAccess("/credit", memberOnly), {
  state: "disabled",
  reason:
    "Requires Credit · Aggregated · View. Ask your organization owner or admin to grant it.",
});
assert.deepEqual(hrefAccess("/credit/book", memberOnly), { state: "hidden" });
assert.equal(isPathVisible("/credit", memberOnly), false);

// ---------------------------------------------------------------------------
// ICAAP workspace (P1). Two independent gates, both fail-closed:
//   1. exact CAP/confidential view authority,
//   2. institution class — an SDI has no Pillar 2 regime, and the backend 404s
//      every ICAAP route for one.
// There is no deployment flag (D-046): ICAAP is part of the product for every
// eligible bank. The nav still hides it until the scope RESOLVES, so a link is
// never offered before the projection says the caller can follow it.
// ---------------------------------------------------------------------------

const icaapCycleId = "6d2b1f7e-0000-4000-8000-000000000001";
const ICAAP_PATHS = [
  "/icaap",
  `/icaap/${icaapCycleId}/overview`,
  `/icaap/${icaapCycleId}/sections/executive_summary`,
  `/icaap/${icaapCycleId}/attachments`,
  // P2's risk & capital tabs. They are listed here so every gate below —
  // CAP/confidential view, the SDI rule, the pre-resolve nav rule — is asserted
  // against them too. A new tab that only the layout knew about would otherwise
  // escape the authority check.
  `/icaap/${icaapCycleId}/risks`,
  `/icaap/${icaapCycleId}/appetite`,
  `/icaap/${icaapCycleId}/pillar2`,
];

assert.equal(moduleForPath("/icaap"), "capital");
assert.equal(
  moduleForPath(`/icaap/${icaapCycleId}/sections/executive_summary`),
  "capital",
);

const icaapEnabled = resolved(true, true);
for (const path of ICAAP_PATHS) {
  assert.equal(isHrefVisible(path, icaapEnabled), true);
  assert.equal(isPathVisible(path, icaapEnabled), true);
}

// Confidential view is the whole gate: an aggregated-only capital scope does
// not reach the institution's own capital assessment.
const icaapAggregatedOnly = resolved(true, true, {
  capitalConfidentialView: false,
});
for (const path of ICAAP_PATHS) {
  assert.deepEqual(hrefAccess(path, icaapAggregatedOnly), { state: "hidden" });
  assert.equal(isPathVisible(path, icaapAggregatedOnly), false);
}

// An SDI tenant never sees it, confidential view notwithstanding.
const icaapSdi = resolved(true, true, {
  institutionClass: "sdi",
});
for (const path of ICAAP_PATHS) {
  assert.deepEqual(hrefAccess(path, icaapSdi), { state: "hidden" });
  assert.equal(isPathVisible(path, icaapSdi), false);
}

// Before the scope resolves the nav hides ICAAP, while the route guard stays
// permissive so a deep-link refresh waits instead of flashing a 404.
const icaapUnresolved: ModuleScope = {
  ...unresolved,
  capitalConfidentialView: true,
};
assert.deepEqual(hrefAccess("/icaap", icaapUnresolved), { state: "hidden" });
assert.equal(isPathVisible("/icaap", icaapUnresolved), true);

// The hub URL redirects a baseline-only member to the root workspace rather
// than 404ing, and never advertises the surface with a grant sentence.
assert.equal(hubRedirectFor("/icaap", memberOnly), "/");
assert.deepEqual(hrefAccess("/icaap", memberOnly), { state: "hidden" });
assert.equal(
  hubRedirectFor(`/icaap/${icaapCycleId}/overview`, memberOnly),
  null,
);

// `/basel/planning` is unchanged by any of the above.
assert.equal(isHrefVisible("/basel/planning", resolved(true, true)), true);
assert.deepEqual(hrefAccess("/basel/planning", aggregatedCapitalOnly), {
  state: "hidden",
});

// ---------------------------------------------------------------------------
// ICAAP cycle tabs (P1-P3)
//
// A tab is offered only when a route file exists behind it, and a declared-but-
// disabled tab must stay out of the strip: a tab whose page does not exist is a
// promise the app cannot keep — the P1 convention that makes a typed URL a
// genuine 404 rather than a blank screen implying the feature shipped. Every
// declared tab now HAS a page, so the rule is checked below in both directions
// rather than by naming the exceptions.
// ---------------------------------------------------------------------------

const enabledSegments = ICAAP_CYCLE_TABS.filter((tab) => tab.enabled).map(
  (tab) => tab.segment,
);
assert.deepEqual(enabledSegments, [
  "overview",
  "sections",
  "risks",
  "appetite",
  "pillar2",
  "stress",
  "review",
  "attachments",
  "filing",
]);

const tabHrefs = icaapTabHrefs(icaapCycleId).map((tab) => tab.href);
for (const segment of [
  "risks",
  "appetite",
  "pillar2",
  "stress",
  "review",
  "filing",
]) {
  assert.ok(
    tabHrefs.includes(`/icaap/${icaapCycleId}/${segment}`),
    `the ${segment} tab must be in the strip`,
  );
}
// A tab declared but not enabled must never reach the strip. The list is empty
// today; the check is kept so that re-declaring a future phase's tab cannot
// quietly offer a route that does not exist.
for (const tab of ICAAP_CYCLE_TABS.filter((entry) => !entry.enabled)) {
  assert.ok(
    !tabHrefs.some((href) => href.endsWith(`/${tab.segment}`)),
    `${tab.segment} is disabled, so it must not be offered`,
  );
}

// The convention, made executable rather than hand-maintained: the list above
// drifted the moment a tab was enabled, and a hand-checked list cannot tell
// whether the page behind a tab exists. Now it is read off the filesystem, so
// enabling a tab without its route file — or deleting a route file behind an
// enabled tab — fails here rather than in a preparer's browser.
const dashboardRoot = __dirname.includes(".test-out")
  ? resolvePath(__dirname, "../..")
  : resolvePath(__dirname, "..");
for (const tab of ICAAP_CYCLE_TABS) {
  const routeFile = joinPath(
    dashboardRoot,
    "app",
    "(app)",
    "icaap",
    "[cycleId]",
    tab.segment,
    "page.tsx",
  );
  assert.equal(
    fileExists(routeFile),
    tab.enabled,
    tab.enabled
      ? `the ${tab.segment} tab is offered but has no page.tsx behind it`
      : `the ${tab.segment} tab has a page.tsx but is not offered — enable it or remove the page`,
  );
}

// Every tab the strip offers passes the same gates as the rest of the module:
// visible only with CAP/confidential view, and never for an SDI (which has no
// Pillar 2 regime at all).
for (const href of tabHrefs) {
  assert.equal(isPathVisible(href, icaapEnabled), true, href);
  assert.equal(isPathVisible(href, icaapAggregatedOnly), false, href);
  assert.equal(isPathVisible(href, icaapSdi), false, href);
}

// ---------------------------------------------------------------------------
// P2 capability projection
//
// Both are exact-capability reads with no fallback: an omitted field is deny,
// and neither is ever satisfied by a scalar role. They gate CONTROLS only — the
// service re-decides, and additionally refuses self-approval (Pillar 2) and a
// reviewer who helped prepare the cycle (independent review), neither of which
// a capability can express.
// ---------------------------------------------------------------------------

const capitalApproverCapabilities = [
  {
    module: "cap",
    sensitivity: "confidential",
    permission: "approve",
    requiresContextualAuthorization: false,
    dataScope: {
      kind: "all",
      branches: [] as string[],
      regions: [] as string[],
    },
  },
] as Parameters<typeof hasEffectiveCapability>[0];

assert.equal(
  hasEffectiveCapability(
    capitalApproverCapabilities,
    "cap",
    "confidential",
    "approve",
  ),
  true,
);
// An edit grant is not an approve grant: that collapse would break the
// maker-checker in the UI before the server ever saw the request.
assert.equal(
  hasEffectiveCapability(
    capitalApproverCapabilities,
    "cap",
    "confidential",
    "edit",
  ),
  false,
);
// A capability whose final answer needs object context is never authority.
assert.equal(
  hasEffectiveCapability(
    [
      {
        module: "audit",
        sensitivity: "confidential",
        permission: "create",
        requiresContextualAuthorization: true,
        dataScope: {
          kind: "all",
          branches: [] as string[],
          regions: [] as string[],
        },
      },
    ] as Parameters<typeof hasEffectiveCapability>[0],
    "audit",
    "confidential",
    "create",
  ),
  false,
);
// Omission is deny, on both new fields.
const withoutP2Capabilities: ModuleScope = resolved(true, true);
assert.notEqual(withoutP2Capabilities.capitalApprove, true);
assert.notEqual(withoutP2Capabilities.auditCreate, true);

// --- Business Intelligence visibility ---------------------------------------
//
// BI is not an institution-type module: it is in no licence class's
// `default_modules`, and it is opened by a view grant on any module the
// catalogue draws from. Two gates therefore have to hold at once — the
// capability that admits it, and the DEPLOYMENT FLAG, which is not a grant and
// so hides the nav entry rather than disabling it with a sentence.

const biCapabilities = [
  {
    module: "credit",
    sensitivity: "aggregated",
    permission: "view",
    requiresContextualAuthorization: false,
    dataScope: {
      kind: "all",
      branches: [] as string[],
      regions: [] as string[],
    },
  },
] as Parameters<typeof effectiveInstitutionModules>[1];

// The entitlement list never contains `bi`, and that must not hide it.
assert.equal(
  effectiveInstitutionModules(
    ["command_center", "risk", "credit"],
    biCapabilities,
  ).has("bi"),
  true,
  "a view grant on a BI source module must admit the BI module",
);

// A module the catalogue declares no member in opens nothing.
assert.equal(
  effectiveInstitutionModules(["data_engine"], [
    {
      module: "data",
      sensitivity: "restricted",
      permission: "view",
      requiresContextualAuthorization: false,
      dataScope: {
        kind: "all",
        branches: [] as string[],
        regions: [] as string[],
      },
    },
  ] as Parameters<typeof effectiveInstitutionModules>[1]).has("bi"),
  false,
  "a Data Engine grant alone must not admit BI",
);

// A grant that is not a view is not a read.
assert.equal(
  effectiveInstitutionModules(["credit"], [
    {
      module: "credit",
      sensitivity: "aggregated",
      permission: "export",
      requiresContextualAuthorization: false,
      dataScope: {
        kind: "all",
        branches: [] as string[],
        regions: [] as string[],
      },
    },
  ] as Parameters<typeof effectiveInstitutionModules>[1]).has("bi"),
  false,
);

const biScope = (biEnabled: boolean | undefined): ModuleScope => ({
  ...resolved(true, true, { biEnabled }),
  modules: new Set([...resolved(true, true).modules!, "bi"]),
});

for (const href of ["/insights", "/dashboards", "/explore"]) {
  assert.equal(moduleForPath(href), "bi", `${href} must map to the BI module`);
  // The flag is on: the entry is offered.
  assert.equal(hrefAccess(href, biScope(true)).state, "enabled", href);
  // The flag is off: there is no grant that would fix it, so it is hidden, not
  // disabled with a sentence to ask for.
  assert.deepEqual(hrefAccess(href, biScope(false)), { state: "hidden" }, href);
  // Not yet known: hidden too, so no BI link flashes into the nav on load.
  assert.deepEqual(
    hrefAccess(href, biScope(undefined)),
    { state: "hidden" },
    href,
  );
  // The ROUTE GUARD is the mirror image: it refuses only on a definite no, so a
  // deep-link refresh does not briefly 404 while the flag is still resolving.
  assert.equal(isPathVisible(href, biScope(true)), true, href);
  assert.equal(isPathVisible(href, biScope(false)), false, href);
  assert.equal(isPathVisible(href, biScope(undefined)), true, href);
}

// --- the ask surface has its OWN deployment flag -----------------------------
//
// `BI_NLQ_ENABLED` is independent of `BI_ENABLED`, and with it off the ask routes
// answer 409 rather than 404 — deliberately, so a switched-off surface is
// distinguishable from an absent one. That makes the nav the only thing that can
// decline to offer the door, so all three flag states are pinned, both ways.

const askScope = (
  nlqEnabled: boolean | undefined,
  biEnabled: boolean | undefined = true,
): ModuleScope => ({
  ...resolved(true, true, { biEnabled, nlqEnabled }),
  modules: new Set([...resolved(true, true).modules!, "bi"]),
});

assert.equal(isAskPath("/explore/ask"), true);
assert.equal(isAskPath("/explore/ask/anything"), true);
assert.equal(isAskPath("/explore"), false);
assert.equal(isAskPath("/explore/measures"), false);
assert.equal(moduleForPath("/explore/ask"), "bi");

// The flag is on: the tab is offered.
assert.equal(hrefAccess("/explore/ask", askScope(true)).state, "enabled");
// The flag is off: there is no grant that would fix it, so it is hidden — not
// disabled with a sentence to ask for.
assert.deepEqual(hrefAccess("/explore/ask", askScope(false)), {
  state: "hidden",
});
// Not yet known: hidden too, so no link flashes into the tab strip on load.
assert.deepEqual(hrefAccess("/explore/ask", askScope(undefined)), {
  state: "hidden",
});
// The ROUTE GUARD is the mirror image: it refuses only on a definite no, so a
// deep-link refresh does not briefly 404 while the flag is still resolving.
assert.equal(isPathVisible("/explore/ask", askScope(true)), true);
assert.equal(isPathVisible("/explore/ask", askScope(false)), false);
assert.equal(isPathVisible("/explore/ask", askScope(undefined)), true);

// The two flags are independent in the direction that matters: BI off closes the
// ask surface whatever the NLQ flag says, because every BI route is 404 then.
assert.deepEqual(hrefAccess("/explore/ask", askScope(true, false)), {
  state: "hidden",
});
assert.equal(isPathVisible("/explore/ask", askScope(true, false)), false);

// And the ask flag closes ONLY the ask surface: the rest of Explore is untouched.
for (const sibling of ["/explore", "/explore/measures", "/insights"]) {
  assert.equal(
    hrefAccess(sibling, askScope(false)).state,
    "enabled",
    `${sibling} must not be closed by the natural-language flag`,
  );
  assert.equal(isPathVisible(sibling, askScope(false)), true, sibling);
}

// A deep link inside a dashboard follows its module.
assert.equal(moduleForPath("/dashboards/board/edit"), "bi");
assert.equal(isPathVisible("/dashboards/board/edit", biScope(false)), false);

// A principal with no BI-source grant never reaches the module, flag or no flag.
const noBiScope: ModuleScope = {
  ...resolved(true, true, { biEnabled: true }),
  modules: new Set(["command_center", "settings"]),
  entitledModules: new Set(["command_center", "settings"]),
};
assert.equal(hrefAccess("/insights", noBiScope).state, "hidden");
assert.equal(isPathVisible("/insights", noBiScope), false);

// A baseline-only member is told which grant opens it, like every other module.
const baselineBi: ModuleScope = {
  ...resolved(false, false, { biEnabled: true }),
  modules: new Set(),
  entitledModules: null,
  organizationModules: new Set(),
  hasInstitutionAuthority: false,
};
const baselineBiAccess = hrefAccess("/insights", baselineBi);
assert.equal(baselineBiAccess.state, "disabled");
assert.match(
  baselineBiAccess.state === "disabled" ? baselineBiAccess.reason : "",
  /Business Intelligence/,
);
assert.equal(hubRedirectFor("/insights", baselineBi), "/");

// ---------------------------------------------------------------------------
// Deployment flags for alerts and scheduled reports (audit A360-2 M3).
//
// The pages may claim the platform is "waiting for figures" ONLY when the
// deployment has said it evaluates alerts. Anything else — a shut flag, a
// backend that does not project the flag, a body that is not an object — must
// read as "not said" or "no", never as the bank's data being late.
// ---------------------------------------------------------------------------

// The reader that used to take these off the raw body is RETIRED — the client
// carries `biAlertsEnabled` / `biSubscriptionsEnabled` now, so the hook reads the
// typed fields and there is no parsing left to unit-test. What still matters is
// the three-valued RULE, which is a property of the hook, so it is asserted where
// the hook expresses it: an explicit `false` is the only thing that may make a
// page say "not judged here", and an error must fail closed.
const hooks = readFileSync(joinPath(dashboardRoot, "lib/api/hooks.ts"), "utf8");
assert.match(
  hooks,
  /alerts:\s*query\.data\?\.biAlertsEnabled/,
  "useBiNotificationCapabilities must read the typed alert flag",
);
assert.match(
  hooks,
  /subscriptions:\s*query\.data\?\.biSubscriptionsEnabled/,
  "useBiNotificationCapabilities must read the typed subscription flag",
);
assert.match(
  hooks,
  /if\s*\(query\.isError\)\s*\{\s*return\s*\{\s*alerts:\s*false,\s*subscriptions:\s*false/,
  "a failed flag read must fail CLOSED — an unreadable flag is not a capability",
);
// The generated client carries both fields since the 2026-09-29 regeneration,
// which is what retired the reader. Pinned so a schema change that drops either
// one fails HERE rather than as a page silently reading `undefined` forever.
{
  // Compiled to dashboard/.test-out/lib, so the repository root is four up.
  const generated = resolvePath(
    __dirname,
    "..",
    "..",
    "..",
    "..",
    "packages",
    "risk-service-api",
    "src",
    "models",
    "FeatureFlagsRead.ts",
  );
  assert.ok(fileExists(generated), `generated model not found: ${generated}`);
  const source = readFileSync(generated, "utf8");
  // INVERTED at the regeneration (AGENTS.md): the reader existed only because
  // `FeatureFlagsRead` predated the two flags. It now carries them, the reader is
  // deleted, and the hook reads the typed fields — so this asserts the typed
  // fields EXIST and the reader has not come back.
  assert.equal(
    source.includes("biAlertsEnabled"),
    true,
    "FeatureFlagsRead no longer carries biAlertsEnabled, so the typed read in " +
      "useBiNotificationCapabilities is reading a field that does not exist",
  );
  assert.ok(
    source.includes("biSubscriptionsEnabled"),
    "FeatureFlagsRead no longer carries biSubscriptionsEnabled",
  );
  const modules = readFileSync(
    joinPath(dashboardRoot, "lib/modules.ts"),
    "utf8",
  );
  assert.equal(
    modules.includes("notificationCapabilitiesFromFeatureFlags"),
    false,
    "the raw-body flag reader is back in lib/modules.ts. The generated client " +
      "carries both flags now, so a hand-written reader beside it is a second, " +
      "unchecked copy (AGENTS.md: delete the interim reader at regeneration).",
  );
}

// ---------------------------------------------------------------------------
// The two notification pages never render a deployment switch as the bank's
// late data, nor a refusal as an absence. Read from source, like the fail-open
// guard: these are `"use client"` pages and cannot be executed here.
// ---------------------------------------------------------------------------
{
  const dashboardRoot = resolvePath(__dirname, "..", "..");
  const alerts = readFileSync(
    joinPath(dashboardRoot, "app", "(app)", "explore", "alerts", "page.tsx"),
    "utf8",
  );
  const subscriptions = readFileSync(
    joinPath(
      dashboardRoot,
      "app",
      "(app)",
      "explore",
      "subscriptions",
      "page.tsx",
    ),
    "utf8",
  );

  // Both pages read the deployment flags.
  assert.ok(
    alerts.includes("useBiNotificationCapabilities("),
    "alerts page reads the flags",
  );
  assert.ok(
    subscriptions.includes("useBiNotificationCapabilities("),
    "subscriptions page reads the flags",
  );
  // "Waiting for figures" is claimed ONLY on a definite yes from the deployment.
  const waiting = [...alerts.matchAll(/"Waiting for figures"/g)];
  assert.equal(waiting.length, 1, "one site decides the 'waiting' copy");
  assert.ok(
    alerts.includes(
      'if (evaluationEnabled === true) return "Waiting for figures";',
    ),
    "'Waiting for figures' must be gated on the deployment saying it evaluates alerts",
  );
  assert.ok(
    alerts.includes(
      'if (evaluationEnabled === false) return "Not judged here";',
    ),
    "a shut flag must read as the deployment's, not the bank's",
  );
  // "Sending" likewise.
  assert.ok(
    subscriptions.includes(
      'if (deliveryEnabled === true) return { label: "Sending"',
    ),
    "'Sending' must be gated on the deployment saying it delivers reports",
  );
  assert.ok(
    subscriptions.includes(
      'if (deliveryEnabled === false) return { label: "Not sending here"',
    ),
    "a shut flag must read as the deployment's, not the bank's",
  );
  assert.doesNotMatch(
    subscriptions,
    /subscription\.isActive \? "Sending" : "Stopped"/,
    "the pill no longer claims 'Sending' from the row alone",
  );

  // A refusal is never an absence: the server's own sentence is consulted FIRST,
  // the grant paraphrase second, and a failed read is named as a failed read.
  for (const [name, source, errorExpr] of [
    ["alerts", alerts, "events.error"],
    ["subscriptions", subscriptions, "deliveries.error"],
  ] as const) {
    const refusal = source.indexOf(`biRefusalSentence(${errorExpr}) ??`);
    const grant = source.indexOf(`isBiAccessDenied(${errorExpr})`);
    assert.ok(
      refusal >= 0,
      `${name}: the history must consult biRefusalSentence`,
    );
    assert.ok(
      grant >= 0,
      `${name}: the history must keep the grant paraphrase`,
    );
    assert.ok(
      refusal < grant,
      `${name}: the server's sentence must be consulted before the grant paraphrase, ` +
        `so a non-grant 403 is never paraphrased as a grant to ask an owner for`,
    );
    assert.ok(
      source.includes(`${errorExpr.split(".")[0]}.isError`),
      `${name}: a failed history read must be named, not shown as an empty history`,
    );
  }
}

console.log(
  "modules.test.ts: binding-controlled navigation and deep links passed.",
);

for (const path of [
  "/forecasting/nii",
  "/forecasting/optimizer",
  "/forecasting/whatif",
]) {
  assert.equal(isPathVisible(path, deniedForecasting), true);
  assert.deepEqual(hrefAccess(path, deniedForecasting), {
    state: "disabled",
    reason:
      "Requires Forecasting · Aggregated · View. Ask an Org Owner to grant access via Settings → Members.",
  });
}
for (const path of [
  "/forecasting",
  "/forecasting/scenario",
  "/forecasting/reverse-stress",
]) {
  const unbound = resolved(true, true, {
    forecastingAggregatedView: false,
    forecastingConfidentialView: false,
  });
  assert.equal(isPathVisible(path, unbound), true);
  assert.equal(hrefAccess(path, unbound).state, "disabled");
  assert.equal(isPathVisible(`${path}/opaque-run-id`, unbound), false);
}
assert.equal(
  isPathVisible("/forecasting/reverse-stress", aggregatedForecastingOnly),
  true,
);

const accountOnlyForecastingScope = resolved(false, false, {
  organizationModules: new Set(["settings"]),
  hasInstitutionAuthority: false,
  modules: new Set(),
  entitledModules: null,
  forecastingAggregatedView: false,
  forecastingConfidentialView: false,
  forecastingRun: false,
});
for (const [path, requirement] of [
  ["/forecasting", "Aggregated"],
  ["/forecasting/assumptions", "Aggregated"],
  ["/forecasting/scenario", "Confidential"],
  ["/forecasting/reverse-stress", "Confidential"],
  ["/forecasting/nii", "Confidential · View and Forecasting · Aggregated"],
  [
    "/forecasting/optimizer",
    "Confidential · View and Forecasting · Aggregated",
  ],
  ["/forecasting/whatif", "Confidential · View and Forecasting · Aggregated"],
]) {
  const expected = {
    state: "disabled",
    reason: `Requires Forecasting · ${requirement} · View. Ask an Org Owner to grant access via Settings → Members.`,
  };
  assert.deepEqual(hrefAccess(path, accountOnlyForecastingScope), expected);
  assert.deepEqual(
    forecastingWorkspaceAccess(path, accountOnlyForecastingScope),
    expected,
  );
  assert.equal(isPathVisible(path, accountOnlyForecastingScope), true);
  assert.equal(isHrefVisible(path, accountOnlyForecastingScope), false);
  const structurallyExcluded = {
    ...accountOnlyForecastingScope,
    entitledModules: new Set<ModuleKey>(["liquidity"]),
  };
  assert.deepEqual(forecastingWorkspaceAccess(path, structurallyExcluded), {
    state: "hidden",
  });
  assert.equal(isPathVisible(path, structurallyExcluded), false);
}
for (const path of [
  "/forecasting/unknown",
  "/forecasting/runs/opaque-run-id",
  "/forecasting/reverse-stress/opaque-run-id",
]) {
  assert.equal(
    forecastingWorkspaceAccess(path, accountOnlyForecastingScope),
    undefined,
  );
  assert.deepEqual(hrefAccess(path, accountOnlyForecastingScope), {
    state: "hidden",
  });
  assert.equal(isPathVisible(path, accountOnlyForecastingScope), false);
}
