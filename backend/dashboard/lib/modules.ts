import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";

/**
 * Institution-type module scoping (docs/sdi.md §3, §6.3).
 *
 * The platform's top-level modules are stable slugs that match the backend
 * institution_types registry's `default_modules` (migration 202608190018). Given
 * the active tenant's scoped module set (`BankRead.institutionTypeDetail
 * .defaultModules`) and its `institution_class`, these PURE helpers decide which
 * routes and nav entries are visible — the single source of truth the three
 * static nav surfaces (Sidebar, CommandPalette, per-module `ModuleTabs`) and the
 * route guard all consult. A universal bank is unscoped (every module visible);
 * a savings-&-loans tenant drops the bank-only modules (docs/sdi.md §3.2).
 */

export type ModuleKey =
  | "command_center"
  | "risk"
  | "alerts"
  | "liquidity"
  | "capital"
  | "credit"
  | "regulatory_reporting"
  | "data_engine"
  | "institution"
  | "reports"
  | "settings"
  | "irrbb"
  | "behavioral"
  | "forecasting"
  | "ftp"
  | "fx"
  | "markets"
  | "positions"
  // Business Intelligence is not an institution-type module: it appears in no
  // `default_modules` set and no licence class is entitled to it. It is the
  // READING surface over the modules a principal already holds, so it is
  // admitted by capability alone — see `effectiveInstitutionModules` and
  // `ENTITLEMENT_EXEMPT_MODULES` below.
  | "bi";

/**
 * Route-prefix → module slug. Longest matching prefix wins; `/` is the Command
 * Center. `/basel` is the "capital" module and `/irr` the "irrbb" module — the
 * URL and the registry slug differ, so the mapping is explicit, never derived
 * from the path.
 */
const ROUTE_MODULES: ReadonlyArray<readonly [string, ModuleKey]> = [
  ["/risk", "risk"],
  ["/insights", "bi"],
  ["/dashboards", "bi"],
  ["/explore", "bi"],
  ["/alerts", "alerts"],
  ["/markets", "markets"],
  ["/positions", "positions"],
  ["/irr", "irrbb"],
  ["/liquidity", "liquidity"],
  ["/fx", "fx"],
  ["/basel", "capital"],
  // The ICAAP workspace is a capital-module surface with its own URL, like
  // `/basel`. An UNMAPPED route is never hidden, so this entry is what makes
  // every scope rule below reach `/icaap` at all.
  ["/icaap", "capital"],
  ["/credit", "credit"],
  ["/ftp", "ftp"],
  ["/forecasting", "forecasting"],
  ["/behavioral", "behavioral"],
  ["/data-engine", "data_engine"],
  ["/reports", "reports"],
  ["/institution", "institution"],
  ["/submissions", "regulatory_reporting"],
  ["/settings", "settings"],
];

function normalize(pathOrHref: string): string {
  const path = (pathOrHref.split("?")[0] || "/").replace(/\/+$/, "");
  return path === "" ? "/" : path;
}

/** True for the root path (query string and trailing slashes ignored). */
export function isRootPath(pathname: string): boolean {
  return normalize(pathname) === "/";
}

/** The module a route path belongs to, or null for a route outside the map. */
export function moduleForPath(pathname: string): ModuleKey | null {
  const path = normalize(pathname);
  if (path === "/") return "command_center";
  let best: readonly [string, ModuleKey] | null = null;
  for (const entry of ROUTE_MODULES) {
    const prefix = entry[0];
    if (path === prefix || path.startsWith(`${prefix}/`)) {
      if (!best || prefix.length > best[0].length) best = entry;
    }
  }
  return best ? best[1] : null;
}

/**
 * Sub-routes an SDI tenant does not see even though the PARENT module is in
 * scope (docs/sdi.md §3.2): Buffer & NSFR are Basel bank liquidity tools, and
 * the full Basel RWA / Capital Structure / Stress / Planning stack is bank-only
 * (an SDI keeps only the simplified `/basel` capital overview). This is
 * nav-level scoping — the SDI capital/liquidity engine reframe is Phase C/D.
 */
const SDI_HIDDEN_SUBROUTES: readonly string[] = [
  "/liquidity/buffer",
  "/liquidity/nsfr",
  "/basel/rwa",
  "/basel/structure",
  "/basel/stress",
  "/basel/planning",
  // The ICAAP is a Pillar 2 obligation on banks. An SDI has no Pillar 2 regime
  // under the BoG framework (DV-006), and the backend 404s every ICAAP route
  // for an SDI tenant — the nav must not offer a door that is walled up.
  "/icaap",
  // The IRRBB standardised framework is calibrated and governed for banks: its
  // whole parameter set is seeded for the bank institution class, so an SDI
  // tenant has no governed value for any of it and the framework would refuse
  // every run. Same rule as the ICAAP above — do not offer a walled-up door.
  "/irr/standardised",
];

const SDI_ONLY_SUBROUTES: readonly string[] = [
  // (empty since credit PR-3 — /basel/exposures redirects to /credit/concentration;
  // the mechanism stays for the next class-scoped subroute.)
];

/**
 * The modules present in EVERY institution type's set (bank ∩ SDI ∩ …). These are
 * safe to show and fetch before the tenant's scope has resolved; the variable
 * modules (IRRBB, FX, FTP, Market Data, Behavioural, Forecasting, Positions) must
 * wait, so none of them flashes in the nav — or fires a request — during the bank
 * load. Keep in step with the `default_modules` intersection in the
 * institution_types registry (migration 202608190018 as amended by 202608210026).
 */
export const CORE_MODULES: ReadonlySet<ModuleKey> = new Set<ModuleKey>([
  "command_center",
  "risk",
  "alerts",
  "liquidity",
  "capital",
  // Credit joined every institution type's default set (credit PR-2,
  // migration 202609010046) — both classes lend, so it is core.
  "credit",
  "regulatory_reporting",
  "data_engine",
  "institution",
  "reports",
  "settings",
]);

export type ModuleScope = {
  /**
   * The scoped module set from the API `default_modules`, or `null` when the bank
   * carries no registry detail. Only meaningful once `isResolved` is true.
   */
  modules: ReadonlySet<ModuleKey> | null;
  /** Institution-type entitlement before effective capabilities are applied. */
  entitledModules?: ReadonlySet<ModuleKey> | null;
  /** Exact organization-level modules projected by the binding evaluator. */
  organizationModules: ReadonlySet<ModuleKey>;
  /** True only when the selected institution has at least one exact capability. */
  hasInstitutionAuthority: boolean;
  institutionClass: string | null;
  /** Exact CAP/aggregated view authority for dashboards and summary checks. */
  capitalAggregatedView?: boolean;
  /** Exact CAP/confidential view authority for plans and run detail. */
  capitalConfidentialView?: boolean;
  /** Exact CAP/restricted view authority for assurance evidence. */
  capitalRestrictedView?: boolean;
  /** Exact CAP/confidential run authority. */
  capitalRun?: boolean;
  /** Exact CAP/confidential create authority (new ICAAP cycles). */
  capitalCreate?: boolean;
  /** Exact CAP/confidential edit authority (sections, blocks, attachments). */
  capitalEdit?: boolean;
  /** Exact CAP/confidential export authority (draft PDF / Word). */
  capitalExport?: boolean;
  /**
   * Exact CAP/confidential APPROVE authority — the checker half of the ICAAP
   * Pillar 2 maker-checker. A UI hint only: the service re-decides every
   * approval, and additionally refuses an approver who authored the revision.
   */
  capitalApprove?: boolean;
  /**
   * Exact AUDIT/confidential create authority, at the ORGANIZATION level —
   * whether this user may record an ICAAP independent review at all. Holding it
   * is still not sufficient: the service refuses a reviewer who participated in
   * preparing the cycle (`reviewer_not_independent`), which is a fact about the
   * cycle that no capability can express.
   */
  auditCreate?: boolean;
  /** Exact FX/aggregated view authority for `/fx` dashboards; scenarios use confidential. */
  fxAggregatedView?: boolean;
  /** Exact FX/confidential view authority for run and analysis detail. */
  fxConfidentialView?: boolean;
  /** Exact FX/confidential run authority for FX engines. */
  fxRun?: boolean;
  /** Exact FTP/aggregated view authority for dashboards and summary lists. */
  ftpAggregatedView?: boolean;
  /** Exact FTP/confidential view authority for run and analysis detail. */
  ftpConfidentialView?: boolean;
  /** Exact FTP/confidential run authority for FTP engines. */
  ftpRun?: boolean;
  /** Exact FCST/aggregated view authority for presets, run summaries, and the live baseline. */
  forecastingAggregatedView?: boolean;
  /** Exact FCST/confidential view authority for full runs and the reverse-stress frontier. */
  forecastingConfidentialView?: boolean;
  /** Exact FCST/confidential run authority for the projection, optimizer, what-if, and reverse stress. */
  forecastingRun?: boolean;
  /**
   * Server-evaluated exact Liquidity capabilities for the selected
   * institution. Omitted/false is deny, so navigation and controls never infer
   * authority from a legacy role or the broader institution-type entitlement.
   */
  liquidityAggregatedView?: boolean;
  liquidityConfidentialView?: boolean;
  riskConfidentialView?: boolean;
  /** Exact IRRBB/aggregated/view authority for every `/irr` dashboard page. */
  irrbbAggregatedView?: boolean;
  /** Exact IRRBB/confidential/view authority for saved-analysis detail and indexes. */
  irrbbConfidentialView?: boolean;
  /** Exact IRRBB/confidential/run authority for regulatory and compute-only engines. */
  irrbbRun?: boolean;
  /**
   * Whether THIS DEPLOYMENT serves Business Intelligence (`GET /feature-flags`
   * → `bi_enabled`). Not a permission: with the flag off every BI route answers
   * 404, so there is no grant a user could be told to ask for and the nav must
   * not offer the door (the same rule the ICAAP flag follows).
   *
   * `undefined` is "not yet known" and is deliberately distinct from `false`:
   * navigation hides BI until the answer arrives, so nothing flashes, while the
   * route guard only refuses on a definite no, so a deep-link refresh does not
   * briefly 404.
   */
  biEnabled?: boolean;
  /**
   * Whether THIS DEPLOYMENT serves natural-language questions
   * (`GET /feature-flags` → `bi_nlq_enabled`). A SECOND deployment flag, not a
   * permission and not implied by `biEnabled`: `BI_NLQ_ENABLED` ships off, so a
   * deployment can serve every BI surface and still refuse to send a reader's
   * words to a model. With it off the ask routes answer 409 rather than 404 —
   * deliberately, so a switched-off surface is distinguishable from an absent
   * one — which means the nav is the only thing that can keep the door from
   * being offered.
   *
   * Three-valued for exactly the reason `biEnabled` is: `undefined` is "not yet
   * known" and hides, so no link flashes; only a definite `false` makes the
   * route guard refuse, so a deep-link refresh does not briefly 404.
   */
  nlqEnabled?: boolean;
  /**
   * False while the bank payload is still loading. Until it flips true the scope
   * is UNKNOWN, so nav + data fetches restrict to `CORE_MODULES` rather than
   * assume "everything" — the fix for the every-module-flashes-on-refresh race.
   */
  isResolved: boolean;
};

/**
 * Whether THIS DEPLOYMENT evaluates alerts and sends subscriptions.
 *
 * Three-valued, and the middle value carries the design: `undefined` means the
 * platform has not answered yet, `false` means it answered no. A page may say
 * "Not judged here" only on an explicit `false` — saying it while the answer is
 * still in flight would blame a deployment decision for a loading state, which
 * is a smaller version of the defect this exists to fix (audit A360-2 M3: the
 * alerts page read "Waiting for figures" about a shut flag, blaming the bank's
 * data).
 *
 * Read from the GENERATED `FeatureFlagsRead` fields since the client was
 * regenerated against the projection (2026-09-29). It was briefly read off the
 * raw response body under the wire names, because the generated model predated
 * the two flags; that reader is retired, and `modules.test.ts` now fails if it
 * comes back.
 */
export type NotificationCapabilities = {
  alerts: boolean | undefined;
  subscriptions: boolean | undefined;
};

export function isBaselineOnlyScope(scope: ModuleScope): boolean {
  return (
    scope.isResolved &&
    !scope.hasInstitutionAuthority &&
    scope.organizationModules.size === 0
  );
}

/** Build a scope from the API `default_modules` list. Empty/absent → null. */
export function moduleSetFrom(
  defaultModules: readonly string[] | null | undefined,
): ReadonlySet<ModuleKey> | null {
  if (!defaultModules || defaultModules.length === 0) return null;
  return new Set(defaultModules as ModuleKey[]);
}

const CAPABILITY_MODULES = {
  liq: ["liquidity"],
  cap: ["capital"],
  // Credit became its own module on 2026-09-22
  // (backend/docs/credit_enforcement_rollout.md). Its shared surfaces — the
  // live-summary row, alerts, window analytics, snapshots — are gated on a
  // CREDIT/aggregated view binding, so that binding alone admits the module.
  // `risk` KEEPS `credit` below: the mirror migration copies every active
  // human `risk` row to `credit`, and a tenant that has not migrated yet still
  // holds only the `risk` row. Either view admits credit; dropping `risk` here
  // would hide the module from today's users for nothing.
  credit: ["credit"],
  irrbb: ["irrbb"],
  fx: ["fx"],
  ftp: ["ftp"],
  fcst: ["forecasting"],
  beh: ["behavioral"],
  data: ["data_engine"],
  reg: ["regulatory_reporting", "reports"],
  risk: ["command_center", "risk", "alerts", "credit", "positions"],
  markets: ["markets"],
  account: [],
  audit: [],
} as const satisfies Record<
  EffectiveCapabilityRead["module"],
  readonly ModuleKey[]
>;

/**
 * The capability modules the BI catalogue draws its members from.
 *
 * Every BI measure and dimension carries its own `(module, sensitivity)` and is
 * evaluated against the caller's bindings by the query route, so a principal
 * with a view grant on ANY of these can read something in BI — and one with a
 * grant on none of them gets an empty catalogue, which is a page with nothing
 * on it rather than a page they should be offered.
 *
 * Deliberately not `account`, `audit`, `data` or `reg`: the catalogue declares
 * no member in those modules, so holding one of them alone opens nothing.
 */
const BI_SOURCE_CAPABILITY_MODULES: ReadonlySet<
  EffectiveCapabilityRead["module"]
> = new Set<EffectiveCapabilityRead["module"]>([
  "credit",
  "risk",
  "cap",
  "liq",
  "irrbb",
  "markets",
  "fcst",
  "fx",
  "ftp",
]);

/**
 * Modules that are NOT institution-type entitlements and must not be filtered
 * against `default_modules`. Only Business Intelligence is one: the registry
 * has no `bi` entry, so an entitlement check would hide it from every tenant.
 */
const ENTITLEMENT_EXEMPT_MODULES: ReadonlySet<ModuleKey> = new Set<ModuleKey>([
  "bi",
]);

export function effectiveInstitutionModules(
  defaultModules: readonly string[] | null | undefined,
  capabilities: readonly EffectiveCapabilityRead[],
): ReadonlySet<ModuleKey> {
  const entitled = moduleSetFrom(defaultModules);
  const modules = new Set<ModuleKey>();
  for (const capability of capabilities) {
    if (capability.permission !== "view") continue;
    for (const capabilityModule of CAPABILITY_MODULES[capability.module]) {
      if (!entitled || entitled.has(capabilityModule)) {
        modules.add(capabilityModule);
      }
    }
    // BI skips the entitlement gate on purpose. It is not in any licence
    // class's module set, and the server authorizes each widget independently
    // against the very grant that admitted the module here — so a BI page can
    // only ever show what the principal could already read elsewhere.
    if (BI_SOURCE_CAPABILITY_MODULES.has(capability.module)) {
      modules.add("bi");
    }
  }
  return modules;
}

export function effectiveOrganizationModules(
  capabilities: readonly EffectiveCapabilityRead[],
): ReadonlySet<ModuleKey> {
  const modules = new Set<ModuleKey>();
  if (
    capabilities.some(
      (capability) =>
        capability.module === "account" &&
        capability.permission === "administer",
    )
  ) {
    modules.add("settings");
  }
  if (hasEffectiveCapability(capabilities, "account", "restricted", "view")) {
    modules.add("institution");
  }
  return modules;
}

export function hasEffectiveCapability(
  capabilities: readonly EffectiveCapabilityRead[],
  module: EffectiveCapabilityRead["module"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
  permission: EffectiveCapabilityRead["permission"],
): boolean {
  return capabilities.some(
    (capability) =>
      !capability.requiresContextualAuthorization &&
      capability.module === module &&
      capability.sensitivity === sensitivity &&
      capability.permission === permission,
  );
}

/**
 * The same read, INCLUDING the capabilities whose final answer needs the object
 * in hand — `approve`, `sign_off` and `submit`, which the evaluator projects
 * with `requires_contextual_authorization` because the server re-decides them
 * against the record being acted on (it refuses the officer who prepared the
 * very return they are trying to approve or file).
 *
 * Use it ONLY to decide whether to OFFER a control, never to claim the act will
 * succeed: the offer is structural eligibility, the outcome is the server's.
 * `hasEffectiveCapability` drops these deliberately and must stay the helper for
 * navigation and for anything that reads as authority — which is why this one is
 * named for what it is. A maker-checker control hidden from everyone is how the
 * ICAAP approve-and-sign affordance became unreachable for every holder of a
 * correct Capital binding: the only helper available excluded exactly the
 * permission the control is about.
 */
export function hasStructuralCapability(
  capabilities: readonly EffectiveCapabilityRead[],
  module: EffectiveCapabilityRead["module"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
  permission: EffectiveCapabilityRead["permission"],
): boolean {
  return capabilities.some(
    (capability) =>
      capability.module === module &&
      capability.sensitivity === sensitivity &&
      capability.permission === permission,
  );
}

function subrouteHidden(path: string, scope: ModuleScope): boolean {
  if (!scope.isResolved) {
    return [...SDI_HIDDEN_SUBROUTES, ...SDI_ONLY_SUBROUTES].some(
      (route) => path === route || path.startsWith(`${route}/`),
    );
  }
  if (scope.institutionClass === "sdi") {
    return SDI_HIDDEN_SUBROUTES.some(
      (r) => path === r || path.startsWith(`${r}/`),
    );
  }
  return SDI_ONLY_SUBROUTES.some((r) => path === r || path.startsWith(`${r}/`));
}

function isIcaapPath(path: string): boolean {
  return path === "/icaap" || path.startsWith("/icaap/");
}

export function isBiPath(path: string): boolean {
  return moduleForPath(normalize(path)) === "bi";
}

/**
 * The natural-language surface. A BI path, and additionally gated on its own
 * deployment flag — see `ModuleScope.nlqEnabled`.
 */
export function isAskPath(path: string): boolean {
  const normalized = normalize(path);
  return (
    normalized === "/explore/ask" || normalized.startsWith("/explore/ask/")
  );
}

function bindingControlledSubrouteHidden(
  path: string,
  scope: ModuleScope,
): boolean {
  if (path === "/liquidity" || path.startsWith("/liquidity/")) {
    const confidentialRoutes = [
      "/liquidity/forecast",
      "/liquidity/monitoring",
      "/liquidity/cfp",
    ];
    if (
      confidentialRoutes.some(
        (route) => path === route || path.startsWith(`${route}/`),
      )
    ) {
      return scope.liquidityConfidentialView !== true;
    }
    if (path === "/liquidity/stress" || path.startsWith("/liquidity/stress/")) {
      return (
        scope.liquidityConfidentialView !== true ||
        scope.riskConfidentialView !== true
      );
    }
    if (scope.institutionClass === "sdi" && path === "/liquidity") {
      return scope.liquidityConfidentialView !== true;
    }
    return scope.liquidityAggregatedView !== true;
  }
  if (
    (path === "/basel/planning" || path.startsWith("/basel/planning/")) &&
    scope.capitalConfidentialView !== true
  ) {
    return true;
  }
  // ICAAP is confidential capital work, and that authority is the whole gate
  // (D-046: the workspace has no deployment flag). "Not yet resolved" is
  // `undefined`, which hides it, so the nav never offers a link before the
  // projection says the caller can follow it.
  if (
    (path === "/icaap" || path.startsWith("/icaap/")) &&
    scope.capitalConfidentialView !== true
  ) {
    return true;
  }
  // A deployment without BI answers every BI route 404, so the route guard
  // refuses too — but only once the flag has actually resolved. `undefined`
  // here is "not yet known", and refusing on that would 404 a deep-link
  // refresh before the answer arrives.
  if (isBiPath(path) && scope.biEnabled === false) return true;
  // Its own flag, and the same asymmetry: refuse only on a definite no. The ask
  // routes answer 409 rather than 404 when the flag is off, so without this the
  // page would load and only fail when the reader typed a question.
  if (isAskPath(path) && scope.nlqEnabled === false) return true;
  if (
    (path === "/basel" ||
      path === "/basel/rwa" ||
      path.startsWith("/basel/rwa/") ||
      path === "/basel/structure" ||
      path.startsWith("/basel/structure/") ||
      path === "/basel/stress" ||
      path.startsWith("/basel/stress/")) &&
    scope.capitalAggregatedView !== true
  ) {
    return true;
  }
  if (scopedModulePermissionReason(path, scope)) return true;
  return false;
}

export type HrefAccess =
  | { state: "enabled" }
  | { state: "disabled"; reason: string }
  | { state: "hidden" };

const LIQUIDITY_AGGREGATED_VIEW = "Liquidity Monitoring · Aggregated · View";
const LIQUIDITY_CONFIDENTIAL_VIEW =
  "Liquidity Monitoring · Confidential · View";
const RISK_CONFIDENTIAL_VIEW = "Risk & Limits · Confidential · View";
const IRRBB_CONFIDENTIAL_RUN = "IRRBB · Confidential · Run";
const FTP_CONFIDENTIAL_RUN = "Funds Transfer Pricing · Confidential · Run";
const FORECASTING_CONFIDENTIAL_RUN = "Forecasting · Confidential · Run";
const FORECASTING_CONFIDENTIAL_VIEW = "Forecasting · Confidential · View";

const MODULE_ENTRY_REQUIREMENTS: Readonly<Record<ModuleKey, string>> = {
  command_center: "Risk & Limits · Aggregated · View",
  risk: "Risk & Limits · Confidential · View",
  alerts: "Risk & Limits · Confidential · View",
  markets: "Markets · Published · View",
  positions: "Risk & Limits · Confidential · View",
  irrbb: "IRRBB · Aggregated · View",
  liquidity: LIQUIDITY_AGGREGATED_VIEW,
  // The sentence names the grant that OPENS the module. Credit is admitted by
  // its own module now (a mirrored `risk` row still works — see
  // CAPABILITY_MODULES), and the grant an Org Owner would issue afresh is the
  // Credit one, matching the rollout contract's exact binding row.
  credit: "Credit · Aggregated · View",
  fx: "Foreign Exchange · Aggregated · View",
  capital: "Basel Capital · Aggregated · View",
  ftp: "Funds Transfer Pricing · Aggregated · View",
  forecasting: "Forecasting · Aggregated · View",
  behavioral: "Behavioral Models · Aggregated · View",
  data_engine: "Data Engine · Restricted · View",
  reports: "Regulatory Reporting · Published · View",
  institution: "Account Administration · Restricted · View",
  regulatory_reporting: "Regulatory Reporting · Published · View",
  settings: "Account Administration · Restricted · Administer",
  // BI has no sentence of its own: every measure and dimension carries the
  // module and sensitivity of the thing it reports on, and the query route
  // requires every one the submitted question touches. What opens the module is
  // a view grant on any module the catalogue draws from, so the sentence names
  // the commonest one rather than inventing a "Business Intelligence" grant
  // nobody can issue.
  bi: "a view grant on a module Business Intelligence reads, such as Credit · Aggregated · View",
};

function permissionReason(permissions: readonly string[]): string | undefined {
  if (permissions.length === 0) return undefined;
  const required =
    permissions.length === 1
      ? permissions[0]
      : `${permissions.slice(0, -1).join(", ")} and ${permissions.at(-1)}`;
  const pronoun = permissions.length === 1 ? "it" : "them";
  return `Requires ${required}. Ask your organization owner or admin to grant ${pronoun}.`;
}

export const IRRBB_CONFIDENTIAL_RUN_REASON = permissionReason([
  IRRBB_CONFIDENTIAL_RUN,
])!;
export const FTP_CONFIDENTIAL_RUN_REASON = permissionReason([
  FTP_CONFIDENTIAL_RUN,
])!;
export const FORECASTING_CONFIDENTIAL_RUN_REASON = permissionReason([
  FORECASTING_CONFIDENTIAL_RUN,
])!;
export const FORECASTING_CONFIDENTIAL_VIEW_REASON = permissionReason([
  FORECASTING_CONFIDENTIAL_VIEW,
])!;

function liquidityPermissionReason(
  path: string,
  scope: ModuleScope,
): string | undefined {
  if (path !== "/liquidity" && !path.startsWith("/liquidity/")) {
    return undefined;
  }

  const missing: string[] = [];
  const confidentialRoutes = [
    "/liquidity/forecast",
    "/liquidity/monitoring",
    "/liquidity/cfp",
    "/liquidity/stress",
  ];
  const requiresConfidential =
    confidentialRoutes.some(
      (route) => path === route || path.startsWith(`${route}/`),
    ) ||
    (scope.institutionClass === "sdi" && path === "/liquidity");

  if (requiresConfidential) {
    if (scope.liquidityConfidentialView !== true) {
      missing.push(LIQUIDITY_CONFIDENTIAL_VIEW);
    }
  } else if (scope.liquidityAggregatedView !== true) {
    missing.push(LIQUIDITY_AGGREGATED_VIEW);
  }
  if (
    (path === "/liquidity/stress" || path.startsWith("/liquidity/stress/")) &&
    scope.riskConfidentialView !== true
  ) {
    missing.push(RISK_CONFIDENTIAL_VIEW);
  }
  return permissionReason(missing);
}

const SCOPED_MODULE_ROUTES = [
  {
    prefix: "/irr",
    label: "IRRBB",
    aggregatedView: "irrbbAggregatedView",
    confidentialView: "irrbbConfidentialView",
    confidentialRoutes: ["/irr/scenarios"],
  },
  {
    prefix: "/fx",
    label: "Foreign Exchange",
    aggregatedView: "fxAggregatedView",
    confidentialView: "fxConfidentialView",
    confidentialRoutes: ["/fx/scenarios"],
  },
  {
    prefix: "/ftp",
    label: "Funds Transfer Pricing",
    aggregatedView: "ftpAggregatedView",
    confidentialView: "ftpConfidentialView",
    confidentialRoutes: ["/ftp/scenarios"],
  },
  {
    // The balance-sheet overview and the assumptions page read presets, run
    // summaries, and the live baseline; every other tab is built on full run
    // detail, so it needs confidential view.
    prefix: "/forecasting",
    label: "Forecasting",
    aggregatedView: "forecastingAggregatedView",
    confidentialView: "forecastingConfidentialView",
    confidentialRoutes: [
      "/forecasting/nii",
      "/forecasting/scenario",
      "/forecasting/whatif",
      "/forecasting/reverse-stress",
      "/forecasting/optimizer",
    ],
  },
] as const;

export function forecastingWorkspaceAccess(
  pathname: string,
  scope: ModuleScope,
): HrefAccess | undefined {
  const path = normalize(pathname);
  if (
    moduleForPath(path) !== "forecasting" ||
    !PUBLIC_MODULE_ROUTES.has(path)
  ) {
    return undefined;
  }
  return hrefAccess(path, scope);
}

function scopedModulePermissionReason(
  path: string,
  scope: ModuleScope,
): string | undefined {
  for (const routePolicy of SCOPED_MODULE_ROUTES) {
    if (
      path !== routePolicy.prefix &&
      !path.startsWith(`${routePolicy.prefix}/`)
    )
      continue;
    const confidential = routePolicy.confidentialRoutes.some(
      (route) => path === route || path.startsWith(`${route}/`),
    );
    const capability = confidential
      ? routePolicy.confidentialView
      : routePolicy.aggregatedView;
    if (routePolicy.prefix === "/forecasting") {
      const missing: string[] = [];
      if (scope[capability] !== true) {
        missing.push(
          `Forecasting · ${confidential ? "Confidential" : "Aggregated"} · View`,
        );
      }
      if (
        [
          "/forecasting/nii",
          "/forecasting/optimizer",
          "/forecasting/whatif",
        ].includes(path) &&
        scope.forecastingAggregatedView !== true
      ) {
        missing.push("Forecasting · Aggregated · View");
      }
      return missing.length
        ? `Requires ${missing.join(" and ")}. Ask an Org Owner to grant access via Settings → Members.`
        : undefined;
    }
    return scope[capability] === true
      ? undefined
      : permissionReason([
          `${routePolicy.label} · ${confidential ? "Confidential" : "Aggregated"} · View`,
        ]);
  }
  return undefined;
}

const ORGANIZATION_ROUTES = new Set<ModuleKey>(["settings", "institution"]);

export function isPersonalSettingsPath(path: string): boolean {
  return path === "/settings/profile" || path.startsWith("/settings/profile/");
}

/**
 * Is a route path visible under this scope? Used by the ROUTE GUARD, which 404s a
 * hidden path — so it stays permissive until the scope resolves (no 404 flash on
 * load) and only blocks a module the RESOLVED tenant is not entitled to. Routes
 * with no module mapping (auth, error, deep utility routes) are never hidden.
 */
export function isPathVisible(pathname: string, scope: ModuleScope): boolean {
  const path = normalize(pathname);
  // A deep-link refresh must wait for scope resolution, never briefly 404.
  if (!scope.isResolved) return true;
  if (isPersonalSettingsPath(path)) return true;
  if (forecastingWorkspaceAccess(path, scope)?.state === "disabled")
    return true;
  const moduleKey = moduleForPath(path);
  if (path === "/" && isBaselineOnlyScope(scope)) {
    return true;
  }
  if (
    moduleKey &&
    ORGANIZATION_ROUTES.has(moduleKey) &&
    !scope.organizationModules.has(moduleKey)
  ) {
    return false;
  }
  if (moduleKey && ORGANIZATION_ROUTES.has(moduleKey)) return true;
  if (
    moduleKey &&
    scope.isResolved &&
    scope.modules &&
    !scope.modules.has(moduleKey)
  ) {
    return false;
  }
  if (bindingControlledSubrouteHidden(path, scope)) return false;
  if (subrouteHidden(path, scope)) return false;
  return true;
}

/**
 * Whether an href is enabled for navigation and data fetching. Disabled links
 * remain visible under the permission-only policy; renderers use `hrefAccess`
 * to distinguish them from structural exclusions.
 */
export function isHrefVisible(href: string, scope: ModuleScope): boolean {
  return hrefAccess(href, scope).state === "enabled";
}

/**
 * Navigation treatment for an href. Structural and object-scope exclusions stay
 * hidden; a resolved Liquidity, IRRBB, FX, FTP, or Forecasting permission gap
 * stays visible but disabled with the exact grant sentence the user needs.
 */
export function hrefAccess(href: string, scope: ModuleScope): HrefAccess {
  if (scope.institutionClass === "sdi" && /[?&]code=BSD/i.test(href))
    return { state: "hidden" };
  const path = normalize(href);
  // Hide class-specific subroutes until the class is known. In particular,
  // Capital is a core module but its Basel and SDI tabs are not interchangeable.
  if (subrouteHidden(path, scope)) return { state: "hidden" };
  if (isPersonalSettingsPath(path)) return { state: "enabled" };
  // ICAAP is decided BEFORE the baseline-only permission sentences. It is
  // gated by a deployment flag as well as by CAP/confidential view, so there
  // is no grant a user could be told to ask for — and on an unflagged
  // deployment the surface must not be named in the nav at all.
  if (isIcaapPath(path) && bindingControlledSubrouteHidden(path, scope)) {
    return { state: "hidden" };
  }
  // Same rule, same reason: a deployment flag is not a grant, so a BI link is
  // hidden rather than disabled-with-a-sentence. `!== true` (not `=== false`)
  // because the nav must not offer the door before the flag has resolved.
  if (isBiPath(path) && scope.biEnabled !== true) return { state: "hidden" };
  // The ask surface's own flag, decided here for the same reason and with the
  // same `!== true`: a deployment flag is not a grant, so there is no sentence a
  // reader could be shown, and the nav must not name the door before the flag
  // has resolved.
  if (isAskPath(path) && scope.nlqEnabled !== true) return { state: "hidden" };
  const moduleKey = moduleForPath(path);
  if (moduleKey) {
    if (isBaselineOnlyScope(scope)) {
      if (moduleKey === "settings") return { state: "enabled" };
      const reason =
        liquidityPermissionReason(path, scope) ??
        scopedModulePermissionReason(path, scope) ??
        (path === "/" || ROUTE_MODULES.some(([route]) => route === path)
          ? permissionReason([MODULE_ENTRY_REQUIREMENTS[moduleKey]])
          : undefined);
      return reason ? { state: "disabled", reason } : { state: "hidden" };
    }
    if (ORGANIZATION_ROUTES.has(moduleKey)) {
      return scope.organizationModules.has(moduleKey)
        ? { state: "enabled" }
        : { state: "hidden" };
    }
    if (!scope.isResolved) {
      return moduleKey === "liquidity"
        ? { state: "hidden" }
        : CORE_MODULES.has(moduleKey)
          ? { state: "enabled" }
          : { state: "hidden" };
    }
    if (
      !scope.hasInstitutionAuthority &&
      !(moduleKey === "forecasting" && PUBLIC_MODULE_ROUTES.has(path))
    ) {
      return { state: "hidden" };
    }
    if (
      scope.entitledModules &&
      !ENTITLEMENT_EXEMPT_MODULES.has(moduleKey) &&
      !scope.entitledModules.has(moduleKey)
    ) {
      return { state: "hidden" };
    }
  }

  const reason =
    liquidityPermissionReason(path, scope) ??
    scopedModulePermissionReason(path, scope);
  if (reason) {
    return { state: "disabled", reason };
  }
  if (bindingControlledSubrouteHidden(path, scope)) {
    return { state: "hidden" };
  }
  if (moduleKey && scope.modules && !scope.modules.has(moduleKey)) {
    return { state: "hidden" };
  }
  return { state: "enabled" };
}

/**
 * Sidebar order of every module home. The root landing resolves against this
 * list so a user is sent to the first surface they would actually see in the
 * nav — never to a route the sidebar hides.
 */
const LANDING_CANDIDATES: readonly string[] = [
  "/",
  "/risk",
  "/alerts",
  "/insights",
  "/dashboards",
  "/explore",
  "/markets",
  "/positions",
  "/irr",
  "/liquidity",
  "/credit",
  "/fx",
  "/basel",
  "/basel/planning",
  "/ftp",
  "/forecasting",
  "/behavioral",
  "/data-engine",
  "/reports",
  "/institution",
  "/submissions",
  "/settings",
];

/**
 * Where a signed-in user lands when they arrive at the root. `/` is the
 * post-sign-in destination for everyone, but the Command Center needs RISK view
 * authority — an Org Owner holding Account administration alone, or a Liquidity
 * Manager, would otherwise be dumped on a 404 by the route guard the moment
 * they signed in (docs/rbac.md §8.3). Returns the first visible surface in
 * sidebar order, falling back to personal settings, which every active session
 * can open; null while the scope is still unresolved.
 */
export function landingPathFor(scope: ModuleScope): string | null {
  if (!scope.isResolved) return null;
  if (isBaselineOnlyScope(scope)) {
    return "/";
  }
  return (
    LANDING_CANDIDATES.find((href) => isHrefVisible(href, scope)) ??
    "/settings/profile"
  );
}

const PUBLIC_MODULE_ROUTES: ReadonlySet<string> = new Set([
  "/alerts",
  "/basel",
  "/basel/exposures",
  "/basel/loan-book",
  "/basel/planning",
  "/basel/rwa",
  "/basel/stress",
  "/basel/structure",
  "/behavioral",
  "/behavioral/deposit-stability",
  "/behavioral/liquidity",
  "/behavioral/nmd-duration",
  "/behavioral/prepayment",
  "/credit",
  "/credit/activity",
  "/credit/book",
  "/credit/concentration",
  "/credit/delinquency",
  "/credit/vintages",
  "/dashboards",
  "/data-engine",
  "/data-engine/adapters",
  "/data-engine/api",
  "/data-engine/database",
  "/data-engine/excel-csv",
  "/data-engine/market-data",
  "/data-engine/positions",
  "/data-engine/t24",
  "/forecasting",
  "/forecasting/assumptions",
  "/forecasting/nii",
  "/forecasting/optimizer",
  "/forecasting/reverse-stress",
  "/forecasting/scenario",
  "/forecasting/whatif",
  "/explore",
  // The two things a reader does with a question they have built in Explore.
  // Public like their hub for the same reason: they are surfaces somebody is
  // sent to (a refused recipient of a confidential report lands on `/explore`),
  // not deep links into another person's data — so a baseline-only member
  // returns to `/` rather than meeting a 404.
  "/explore/alerts",
  "/explore/subscriptions",
  "/ftp",
  "/ftp/expost",
  "/ftp/lines",
  "/ftp/products",
  "/ftp/rules",
  "/ftp/scenarios",
  "/fx",
  "/fx/forwards",
  "/fx/hedges",
  "/fx/limits",
  "/fx/scenarios",
  "/fx/var",
  "/icaap",
  "/insights",
  "/institution",
  "/institution/history",
  "/institution/outlets",
  "/institution/parties",
  "/institution/products",
  "/institution/registers",
  "/irr",
  "/irr/gaps",
  "/irr/limits",
  "/irr/scenarios",
  "/irr/sensitivity",
  "/irr/standardised",
  "/liquidity",
  "/liquidity/buffer",
  "/liquidity/cfp",
  "/liquidity/forecast",
  "/liquidity/monitoring",
  "/liquidity/nsfr",
  "/liquidity/stress",
  "/markets",
  "/positions",
  "/reports",
  "/reports/analyses",
  "/reports/board-pack",
  "/reports/stress-board-pack",
  "/risk",
  "/submissions",
  "/submissions/approvals",
  "/submissions/calendar",
  "/submissions/compare",
  "/submissions/history",
  "/submissions/returns",
  "/submissions/settings",
  "/submissions/signatures",
  "/submissions/templates",
]);

/**
 * Where a hub URL should send a user it is hidden from, or null to 404.
 *
 * Hub URLs are destinations people type or are sent to rather than deep links
 * into someone else's data, so a hidden one redirects instead of 404ing:
 *   - `/`         → the first visible surface (`landingPathFor`);
 *   - `/settings` → personal settings, which every active session can open,
 *                   when organization settings need authority the user lacks.
 * Baseline-only members return from the explicit public-route allow-list to
 * `/`; structural exclusions and hidden object-specific paths stay not-found
 * (docs/rbac.md §8.2).
 */
export function hubRedirectFor(
  pathname: string,
  scope: ModuleScope,
): string | null {
  const path = normalize(pathname);
  if (!scope.isResolved) return null;
  if (path === "/") {
    const landing = landingPathFor(scope);
    return landing && landing !== "/" ? landing : null;
  }
  if (path === "/settings") return "/settings/profile";
  const moduleKey = moduleForPath(path);
  if (
    moduleKey &&
    PUBLIC_MODULE_ROUTES.has(path) &&
    !subrouteHidden(path, scope) &&
    (!scope.entitledModules ||
      ENTITLEMENT_EXEMPT_MODULES.has(moduleKey) ||
      scope.entitledModules.has(moduleKey)) &&
    isBaselineOnlyScope(scope)
  ) {
    return "/";
  }
  return null;
}
