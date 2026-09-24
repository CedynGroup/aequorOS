import assert from "node:assert/strict";
import {
  effectiveInstitutionModules,
  effectiveOrganizationModules,
  forecastingWorkspaceAccess,
  hasEffectiveCapability,
  hrefAccess,
  isHrefVisible,
  isPersonalSettingsPath,
  isPathVisible,
  hubRedirectFor,
  moduleForPath,
  isRootPath,
  landingPathFor,
  type ModuleKey,
  type ModuleScope,
} from "./modules";
import { existsSync as fileExists } from "node:fs";
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
    "markets",
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
    "markets",
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
  marketsPublishedView: true,
  marketsConfidentialView: true,
  marketsRestrictedView: true,
  marketsUpload: true,
  marketsOverlayCreate: true,
  marketsOverlayEdit: true,
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
// Markets enters on the published tier; a confidential-only grant (ratings,
// overlays) does not open the hub, and the reason names the published view.
const deniedMarkets = resolved(true, true, {
  marketsPublishedView: false,
  marketsConfidentialView: true,
});
assert.equal(isHrefVisible("/markets", deniedMarkets), false);
assert.equal(isPathVisible("/markets", deniedMarkets), false);
assert.deepEqual(hrefAccess("/markets", deniedMarkets), {
  state: "disabled",
  reason:
    "Requires Markets · Published · View. Ask your organization owner or admin to grant it.",
});
const publishedMarketsOnly = resolved(true, true, {
  marketsConfidentialView: false,
  marketsRestrictedView: false,
  marketsUpload: false,
  marketsOverlayCreate: false,
  marketsOverlayEdit: false,
});
assert.equal(isHrefVisible("/markets", publishedMarketsOnly), true);
assert.equal(isPathVisible("/markets?tab=curves", publishedMarketsOnly), true);
assert.deepEqual(hrefAccess("/markets", publishedMarketsOnly), {
  state: "enabled",
});

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
} as const;
for (const [capabilities, visible] of [
  [[accountView], true],
  [[], false],
  [[{ ...accountView, permission: "administer" }], false],
  [[{ ...accountView, sensitivity: "aggregated" }], false],
  [[{ ...accountView, requiresContextualAuthorization: true }], false],
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
