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
  // Grantable vocabulary whose consuming surface (institution master data)
  // still enforces through Account; an Institution binding opens nothing
  // until that surface cuts over to it.
  institution: [],
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

/**
 * A deployment without BI answers every BI route 404, so the route guard
 * refuses too — but only once the flag has actually resolved. `undefined` here
 * is "not yet known", and refusing on that would 404 a deep-link refresh
 * before the answer arrives. The ask surface has its own flag and the same
 * asymmetry: its routes answer 409 rather than 404 when the flag is off, so
 * without this the page would load and only fail when the reader typed a
 * question.
 */
function deploymentFlagHidden(path: string, scope: ModuleScope): boolean {
  if (isBiPath(path) && scope.biEnabled === false) return true;
  return isAskPath(path) && scope.nlqEnabled === false;
}

function bindingControlledSubrouteHidden(
  path: string,
  scope: ModuleScope,
): boolean {
  if (deploymentFlagHidden(path, scope)) return true;
  return bindingRequirements(path, scope).length > 0;
}

export type HrefAccess =
  | { state: "enabled" }
  | { state: "disabled"; reason: string }
  | { state: "hidden" };

export type AccessRequirement = {
  moduleScope: EffectiveCapabilityRead["module"];
  moduleLabel: string;
  sensitivityScope: EffectiveCapabilityRead["sensitivity"];
  sensitivityLabel: string;
  permission: EffectiveCapabilityRead["permission"];
  permissionLabel: string;
};

function requirement(
  moduleScope: AccessRequirement["moduleScope"],
  moduleLabel: string,
  sensitivityScope: AccessRequirement["sensitivityScope"],
  sensitivityLabel: string,
  permission: AccessRequirement["permission"] = "view",
  permissionLabel = "View",
): AccessRequirement {
  return {
    moduleScope,
    moduleLabel,
    sensitivityScope,
    sensitivityLabel,
    permission,
    permissionLabel,
  };
}

function requirementLabel(item: AccessRequirement): string {
  return `${item.moduleLabel} · ${item.sensitivityLabel} · ${item.permissionLabel}`;
}

const LIQUIDITY_AGGREGATED_VIEW = requirement(
  "liq",
  "Liquidity Monitoring",
  "aggregated",
  "Aggregated",
);
const LIQUIDITY_CONFIDENTIAL_VIEW = requirement(
  "liq",
  "Liquidity Monitoring",
  "confidential",
  "Confidential",
);
const RISK_AGGREGATED_VIEW = requirement(
  "risk",
  "Risk & Limits",
  "aggregated",
  "Aggregated",
);
const RISK_CONFIDENTIAL_VIEW = requirement(
  "risk",
  "Risk & Limits",
  "confidential",
  "Confidential",
);
const CREDIT_AGGREGATED_VIEW = requirement(
  "credit",
  "Credit",
  "aggregated",
  "Aggregated",
);
const CAPITAL_AGGREGATED_VIEW = requirement(
  "cap",
  "Basel Capital",
  "aggregated",
  "Aggregated",
);
const CAPITAL_CONFIDENTIAL_VIEW = requirement(
  "cap",
  "Basel Capital",
  "confidential",
  "Confidential",
);
const IRRBB_AGGREGATED_VIEW = requirement(
  "irrbb",
  "IRRBB",
  "aggregated",
  "Aggregated",
);
const IRRBB_CONFIDENTIAL_VIEW = requirement(
  "irrbb",
  "IRRBB",
  "confidential",
  "Confidential",
);
const FX_AGGREGATED_VIEW = requirement(
  "fx",
  "Foreign Exchange",
  "aggregated",
  "Aggregated",
);
const FX_CONFIDENTIAL_VIEW = requirement(
  "fx",
  "Foreign Exchange",
  "confidential",
  "Confidential",
);
const REGULATORY_PUBLISHED_VIEW = requirement(
  "reg",
  "Regulatory Reporting",
  "published",
  "Published",
);
/** The exact capability that opens each module's home surface. */
const MODULE_ENTRY_REQUIREMENTS: Readonly<
  Record<Exclude<ModuleKey, "access" | "settings" | "bi">, AccessRequirement>
> = {
  command_center: RISK_AGGREGATED_VIEW,
  risk: RISK_CONFIDENTIAL_VIEW,
  alerts: RISK_CONFIDENTIAL_VIEW,
  markets: requirement("markets", "Markets", "published", "Published"),
  positions: RISK_CONFIDENTIAL_VIEW,
  irrbb: IRRBB_AGGREGATED_VIEW,
  liquidity: LIQUIDITY_AGGREGATED_VIEW,
  // The requirement names the grant that OPENS the module. Credit is admitted
  // by its own module now (a mirrored `risk` row still works — see
  // CAPABILITY_MODULES), and the grant an Org Owner would issue afresh is the
  // Credit one, matching the rollout contract's exact binding row.
  credit: CREDIT_AGGREGATED_VIEW,
  fx: FX_AGGREGATED_VIEW,
  capital: CAPITAL_AGGREGATED_VIEW,
  ftp: requirement("ftp", "Funds Transfer Pricing", "aggregated", "Aggregated"),
  forecasting: requirement("fcst", "Forecasting", "aggregated", "Aggregated"),
  behavioral: requirement(
    "beh",
    "Behavioral Models",
    "aggregated",
    "Aggregated",
  ),
  data_engine: requirement("data", "Data Engine", "restricted", "Restricted"),
  reports: REGULATORY_PUBLISHED_VIEW,
  institution: requirement(
    "account",
    "Account Administration",
    "restricted",
    "Restricted",
  ),
  regulatory_reporting: REGULATORY_PUBLISHED_VIEW,
};

/**
 * BI has no requirement of its own: every measure and dimension carries the
 * module and sensitivity of the thing it reports on, and the query route
 * requires every one the submitted question touches. What opens the module is
 * a view grant on any module the catalogue draws from, so the sentence names
 * the commonest one rather than inventing a "Business Intelligence" grant
 * nobody can issue — and, for the same reason, there is no single capability
 * a reader could request for it.
 */
const BI_ENTRY_REQUIREMENT = `a view grant on a module Business Intelligence reads, such as ${requirementLabel(CREDIT_AGGREGATED_VIEW)}`;

function moduleEntryReason(
  moduleKey: Exclude<ModuleKey, "access" | "settings">,
): string | undefined {
  return moduleKey === "bi"
    ? permissionReason([BI_ENTRY_REQUIREMENT])
    : requirementsReason([MODULE_ENTRY_REQUIREMENTS[moduleKey]]);
}

function permissionReason(permissions: readonly string[]): string | undefined {
  if (permissions.length === 0) return undefined;
  const required =
    permissions.length === 1
      ? permissions[0]
      : `${permissions.slice(0, -1).join(", ")} and ${permissions.at(-1)}`;
  const pronoun = permissions.length === 1 ? "it" : "them";
  return `Requires ${required}. Ask your organization owner or admin to grant ${pronoun}.`;
}

function requirementsReason(
  requirements: readonly AccessRequirement[],
): string | undefined {
  return permissionReason(requirements.map(requirementLabel));
}

export const IRRBB_CONFIDENTIAL_RUN_REASON = permissionReason([
  "IRRBB · Confidential · Run",
])!;
export const FTP_CONFIDENTIAL_RUN_REASON = permissionReason([
  "Funds Transfer Pricing · Confidential · Run",
])!;

function underRoute(path: string, routes: readonly string[]): boolean {
  return routes.some((route) => path === route || path.startsWith(`${route}/`));
}

/**
 * Exact capabilities a Liquidity route needs that this scope lacks. The
 * institution class matters: an SDI's `/liquidity` home is the confidential
 * view (docs/sdi.md §3.2), a bank's is the aggregated one.
 */
function liquidityRequirements(
  path: string,
  scope: ModuleScope,
): AccessRequirement[] {
  if (!underRoute(path, ["/liquidity"])) return [];
  const missing: AccessRequirement[] = [];
  const stress = underRoute(path, ["/liquidity/stress"]);
  const requiresConfidential =
    stress ||
    underRoute(path, [
      "/liquidity/forecast",
      "/liquidity/monitoring",
      "/liquidity/cfp",
    ]) ||
    (scope.institutionClass === "sdi" && path === "/liquidity");
  if (requiresConfidential) {
    if (scope.liquidityConfidentialView !== true) {
      missing.push(LIQUIDITY_CONFIDENTIAL_VIEW);
    }
  } else if (scope.liquidityAggregatedView !== true) {
    missing.push(LIQUIDITY_AGGREGATED_VIEW);
  }
  if (stress && scope.riskConfidentialView !== true) {
    missing.push(RISK_CONFIDENTIAL_VIEW);
  }
  return missing;
}

function liquidityPermissionReason(
  path: string,
  scope: ModuleScope,
): string | undefined {
  return requirementsReason(liquidityRequirements(path, scope));
}

/** Basel routes split by exact CAP sensitivity: planning is confidential, the
 * overview and the RWA / structure / stress stack are aggregated. */
function capitalRequirements(
  path: string,
  scope: ModuleScope,
): AccessRequirement[] {
  if (
    underRoute(path, ["/basel/planning", "/icaap"]) &&
    scope.capitalConfidentialView !== true
  ) {
    return [CAPITAL_CONFIDENTIAL_VIEW];
  }
  if (
    (path === "/basel" ||
      underRoute(path, ["/basel/rwa", "/basel/structure", "/basel/stress"])) &&
    scope.capitalAggregatedView !== true
  ) {
    return [CAPITAL_AGGREGATED_VIEW];
  }
  return [];
}

const SCOPED_MODULE_ROUTES = [
  {
    prefix: "/irr",
    aggregatedView: "irrbbAggregatedView",
    confidentialView: "irrbbConfidentialView",
    aggregated: IRRBB_AGGREGATED_VIEW,
    confidential: IRRBB_CONFIDENTIAL_VIEW,
    confidentialRoutes: ["/irr/scenarios"],
  },
  {
    prefix: "/fx",
    aggregatedView: "fxAggregatedView",
    confidentialView: "fxConfidentialView",
    aggregated: FX_AGGREGATED_VIEW,
    confidential: FX_CONFIDENTIAL_VIEW,
    confidentialRoutes: ["/fx/scenarios"],
  },
  {
    prefix: "/ftp",
    aggregated: requirement(
      "ftp",
      "Funds Transfer Pricing",
      "aggregated",
      "Aggregated",
    ),
    confidential: requirement(
      "ftp",
      "Funds Transfer Pricing",
      "confidential",
      "Confidential",
    ),
    aggregatedView: "ftpAggregatedView",
    confidentialView: "ftpConfidentialView",
    confidentialRoutes: ["/ftp/scenarios"],
  },
] as const;

function scopedModuleRequirements(
  path: string,
  scope: ModuleScope,
): AccessRequirement[] {
  for (const routePolicy of SCOPED_MODULE_ROUTES) {
    if (!underRoute(path, [routePolicy.prefix])) continue;
    const confidential = underRoute(path, routePolicy.confidentialRoutes);
    const capability = confidential
      ? routePolicy.confidentialView
      : routePolicy.aggregatedView;
    return scope[capability] === true
      ? []
      : [confidential ? routePolicy.confidential : routePolicy.aggregated];
  }
  return [];
}

function scopedModulePermissionReason(
  path: string,
  scope: ModuleScope,
): string | undefined {
  return requirementsReason(scopedModuleRequirements(path, scope));
}

/**
 * Every exact capability a binding-controlled route needs that this scope
 * lacks — the ONE rule set the route guard, the disabled-link tooltips and the
 * access-denied page all read, so they can never disagree about what hides a
 * route or what is missing.
 */
function bindingRequirements(
  path: string,
  scope: ModuleScope,
): AccessRequirement[] {
  return [
    ...liquidityRequirements(path, scope),
    ...capitalRequirements(path, scope),
    ...scopedModuleRequirements(path, scope),
  ];
}

const ORGANIZATION_ROUTES = new Set<ModuleKey>(["institution"]);

export function isAccessPath(path: string): boolean {
  return path === "/access" || path.startsWith("/access/");
}

export function isPersonalSettingsPath(path: string): boolean {
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
  if (isPersonalSettingsPath(path)) return true;
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
  // Keep ICAAP navigation hidden without CAP/confidential view. Its public
  // hub can still explain the requirement through the shared denied-page rules.
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
  if (moduleKey && moduleKey !== "access" && moduleKey !== "settings") {
    if (isBaselineOnlyScope(scope)) {
      const reason =
        liquidityPermissionReason(path, scope) ??
        scopedModulePermissionReason(path, scope) ??
        (path === "/" || ROUTE_MODULES.some(([route]) => route === path)
          ? moduleEntryReason(moduleKey)
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
    if (!scope.hasInstitutionAuthority) return { state: "hidden" };
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
  "/access",
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

export const PUBLIC_MODULE_ROUTES: ReadonlySet<string> = new Set([
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
 * The root landing router redirects to the first visible surface when needed.
 * Public-route denials and object-existence protection follow docs/rbac.md §8.2.
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
  // A BI hub has no access-denied page — no single grant opens it — so a
  // baseline-only member returns to `/` rather than meeting a 404.
  if (
    isBiPath(path) &&
    PUBLIC_MODULE_ROUTES.has(path) &&
    !deploymentFlagHidden(path, scope) &&
    isBaselineOnlyScope(scope)
  ) {
    return "/";
  }
  return null;
}

export type AccessDeniedRoute = {
  title: string;
  reason: string;
  requirements: readonly AccessRequirement[];
};

function routeTitle(path: string, moduleKey: ModuleKey): string {
  const exact: Record<string, string> = {
    "/": "Command Center",
    "/fx": "Foreign Exchange",
    "/irr": "IRRBB",
    "/liquidity": "Liquidity",
    "/liquidity/stress": "Liquidity stress scenarios",
    "/reports": "Reports",
  };
  if (exact[path]) return exact[path];
  const leaf = path.split("/").filter(Boolean).at(-1);
  if (leaf) {
    return leaf
      .split("-")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ");
  }
  return moduleKey;
}

/**
 * Public module structure may name the missing authority. Object routes and
 * structural exclusions deliberately return null and remain not-found. The
 * requirements come from the same rules `isPathVisible` applies — the
 * binding-controlled subroute rules first (institution class included), then
 * the module's entry capability — so whatever hides a public route also names
 * what is missing.
 */
export function accessDeniedForPath(
  pathname: string,
  scope: ModuleScope,
): AccessDeniedRoute | null {
  const path = normalize(pathname);
  if (!scope.isResolved || !PUBLIC_MODULE_ROUTES.has(path)) return null;
  const moduleKey = moduleForPath(path);
  // Transitional exception: BI routes are gated by BI_ENABLED; access-denied
  // pages and request-access support are deferred until BI is reviewed.
  if (
    !moduleKey ||
    moduleKey === "access" ||
    moduleKey === "settings" ||
    moduleKey === "bi"
  )
    return null;
  if (subrouteHidden(path, scope) || deploymentFlagHidden(path, scope))
    return null;
  if (scope.entitledModules && !scope.entitledModules.has(moduleKey))
    return null;

  let requirements: AccessRequirement[];
  if (ORGANIZATION_ROUTES.has(moduleKey)) {
    requirements = scope.organizationModules.has(moduleKey)
      ? []
      : [MODULE_ENTRY_REQUIREMENTS[moduleKey]];
  } else {
    requirements = bindingRequirements(path, scope);
    if (
      requirements.length === 0 &&
      scope.modules &&
      !scope.modules.has(moduleKey)
    ) {
      requirements = [MODULE_ENTRY_REQUIREMENTS[moduleKey]];
    }
  }
  if (requirements.length === 0) return null;
  return {
    title: routeTitle(path, moduleKey),
    reason: requirementsReason(requirements)!,
    requirements,
  };
}

export function accessRequestRequirements(
  pathname: string,
  capabilities: readonly EffectiveCapabilityRead[],
  institutionClass: string | null,
): readonly AccessRequirement[] {
  const denied = accessDeniedForPath(pathname, {
    modules: new Set(),
    organizationModules: new Set(),
    hasInstitutionAuthority: false,
    institutionClass,
    isResolved: true,
  });
  return (denied?.requirements ?? []).filter(
    (required) =>
      !hasEffectiveCapability(
        capabilities,
        required.moduleScope,
        required.sensitivityScope,
        required.permission,
      ),
  );
}
