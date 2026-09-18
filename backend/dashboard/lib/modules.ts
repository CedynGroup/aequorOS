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
  | "access"
  | "settings"
  | "irrbb"
  | "behavioral"
  | "forecasting"
  | "ftp"
  | "fx"
  | "markets"
  | "positions";

/**
 * Route-prefix → module slug. Longest matching prefix wins; `/` is the Command
 * Center. `/basel` is the "capital" module and `/irr` the "irrbb" module — the
 * URL and the registry slug differ, so the mapping is explicit, never derived
 * from the path.
 */
const ROUTE_MODULES: ReadonlyArray<readonly [string, ModuleKey]> = [
  ["/risk", "risk"],
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
  ["/access", "access"],
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
  "access",
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
   * False while the bank payload is still loading. Until it flips true the scope
   * is UNKNOWN, so nav + data fetches restrict to `CORE_MODULES` rather than
   * assume "everything" — the fix for the every-module-flashes-on-refresh race.
   */
  isResolved: boolean;
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
  irrbb: ["irrbb"],
  fx: ["fx"],
  ftp: ["ftp"],
  fcst: ["forecasting"],
  beh: ["behavioral"],
  data: ["data_engine"],
  reg: ["regulatory_reporting", "reports"],
  risk: ["command_center", "risk", "alerts", "credit", "positions"],
  markets: ["markets"],
  // Grantable vocabulary whose consuming surfaces still enforce through the
  // existing Risk & Limits and Account gates; a Credit or Institution
  // binding opens nothing until those surfaces cut over to it.
  credit: [],
  institution: [],
  account: [],
  audit: [],
} as const satisfies Record<
  EffectiveCapabilityRead["module"],
  readonly ModuleKey[]
>;

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

const MODULE_ENTRY_REQUIREMENTS: Readonly<Record<ModuleKey, string>> = {
  command_center: "Risk & Limits · Aggregated · View",
  risk: "Risk & Limits · Confidential · View",
  alerts: "Risk & Limits · Confidential · View",
  markets: "Markets · Published · View",
  positions: "Risk & Limits · Confidential · View",
  irrbb: "IRRBB · Aggregated · View",
  liquidity: LIQUIDITY_AGGREGATED_VIEW,
  credit: "Risk & Limits · Confidential · View",
  fx: "Foreign Exchange · Aggregated · View",
  capital: "Basel Capital · Aggregated · View",
  ftp: "Funds Transfer Pricing · Aggregated · View",
  forecasting: "Forecasting · Aggregated · View",
  behavioral: "Behavioral Models · Aggregated · View",
  data_engine: "Data Engine · Restricted · View",
  reports: "Regulatory Reporting · Published · View",
  institution: "Account Administration · Restricted · View",
  regulatory_reporting: "Regulatory Reporting · Published · View",
  access: "Account Administration · Restricted · Administer",
  settings: "Account Administration · Restricted · Administer",
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
] as const;

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
    return scope[capability] === true
      ? undefined
      : permissionReason([
          `${routePolicy.label} · ${confidential ? "Confidential" : "Aggregated"} · View`,
        ]);
  }
  return undefined;
}

const ORGANIZATION_ROUTES = new Set<ModuleKey>(["institution"]);

export function isAccessPath(path: string): boolean {
  return path === "/access" || path.startsWith("/access/");
}

export function isPersonalSettingsPath(path: string): boolean {
  return path === "/settings" || path.startsWith("/settings/");
}

function isSettingsPath(path: string): boolean {
  return path === "/settings" || path.startsWith("/settings/");
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
  if (isAccessPath(path)) return true;
  if (isSettingsPath(path)) return true;
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
 * hidden; a resolved Liquidity, IRRBB, or FX permission gap stays visible but
 * disabled with the exact grant sentence the user needs.
 */
export function hrefAccess(href: string, scope: ModuleScope): HrefAccess {
  if (scope.institutionClass === "sdi" && /[?&]code=BSD/i.test(href))
    return { state: "hidden" };
  const path = normalize(href);
  // Hide class-specific subroutes until the class is known. In particular,
  // Capital is a core module but its Basel and SDI tabs are not interchangeable.
  if (subrouteHidden(path, scope)) return { state: "hidden" };
  if (isAccessPath(path)) return { state: "enabled" };
  if (isPersonalSettingsPath(path)) return { state: "enabled" };
  // ICAAP is decided BEFORE the baseline-only permission sentences. It is
  // gated by a deployment flag as well as by CAP/confidential view, so there
  // is no grant a user could be told to ask for — and on an unflagged
  // deployment the surface must not be named in the nav at all.
  if (isIcaapPath(path) && bindingControlledSubrouteHidden(path, scope)) {
    return { state: "hidden" };
  }
