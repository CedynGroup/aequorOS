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
  /** Exact FX/aggregated view authority for `/fx` dashboards; scenarios use confidential. */
  fxAggregatedView?: boolean;
  /** Exact FX/confidential view authority for run and analysis detail. */
  fxConfidentialView?: boolean;
  /** Exact FX/confidential run authority for FX engines. */
  fxRun?: boolean;
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
  account: ["institution"],
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
  return capabilities.some(
    (capability) =>
      capability.module === "account" && capability.permission === "administer",
  )
    ? new Set<ModuleKey>(["settings"])
    : new Set<ModuleKey>();
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

const ORGANIZATION_ROUTES = new Set<ModuleKey>(["settings"]);

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
  const moduleKey = moduleForPath(path);
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
  if (isPersonalSettingsPath(path)) return { state: "enabled" };
  const moduleKey = moduleForPath(path);
  if (moduleKey) {
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
    if (!scope.hasInstitutionAuthority) return { state: "hidden" };
    if (scope.entitledModules && !scope.entitledModules.has(moduleKey)) {
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
  return (
    LANDING_CANDIDATES.find((href) => isHrefVisible(href, scope)) ??
    "/settings/profile"
  );
}

/**
 * Where a hub URL should send a user it is hidden from, or null to 404.
 *
 * Two URLs are destinations people type or are sent to rather than deep links
 * into someone else's data, so a hidden one redirects instead of 404ing:
 *   - `/`         → the first visible surface (`landingPathFor`);
 *   - `/settings` → personal settings, which every active session can open,
 *                   when organization settings need authority the user lacks.
 * Every other hidden path stays not-found (docs/rbac.md §8.2).
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
  return null;
}
