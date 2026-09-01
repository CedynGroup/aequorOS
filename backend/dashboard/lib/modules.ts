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
  | 'command_center'
  | 'risk'
  | 'alerts'
  | 'liquidity'
  | 'capital'
  | 'credit'
  | 'regulatory_reporting'
  | 'data_engine'
  | 'institution'
  | 'reports'
  | 'settings'
  | 'irrbb'
  | 'behavioral'
  | 'forecasting'
  | 'ftp'
  | 'fx'
  | 'markets'
  | 'positions';

/**
 * Route-prefix → module slug. Longest matching prefix wins; `/` is the Command
 * Center. `/basel` is the "capital" module and `/irr` the "irrbb" module — the
 * URL and the registry slug differ, so the mapping is explicit, never derived
 * from the path.
 */
const ROUTE_MODULES: ReadonlyArray<readonly [string, ModuleKey]> = [
  ['/risk', 'risk'],
  ['/alerts', 'alerts'],
  ['/markets', 'markets'],
  ['/positions', 'positions'],
  ['/irr', 'irrbb'],
  ['/liquidity', 'liquidity'],
  ['/fx', 'fx'],
  ['/basel', 'capital'],
  ['/credit', 'credit'],
  ['/ftp', 'ftp'],
  ['/forecasting', 'forecasting'],
  ['/behavioral', 'behavioral'],
  ['/data-engine', 'data_engine'],
  ['/reports', 'reports'],
  ['/institution', 'institution'],
  ['/submissions', 'regulatory_reporting'],
  ['/settings', 'settings'],
];

function normalize(pathOrHref: string): string {
  const path = (pathOrHref.split('?')[0] || '/').replace(/\/+$/, '');
  return path === '' ? '/' : path;
}

/** The module a route path belongs to, or null for a route outside the map. */
export function moduleForPath(pathname: string): ModuleKey | null {
  const path = normalize(pathname);
  if (path === '/') return 'command_center';
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
  '/liquidity/buffer',
  '/liquidity/nsfr',
  '/basel/rwa',
  '/basel/structure',
  '/basel/stress',
  '/basel/planning',
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
  'command_center',
  'risk',
  'alerts',
  'liquidity',
  'capital',
  // Credit joined every institution type's default set (credit PR-2,
  // migration 202609010046) — both classes lend, so it is core.
  'credit',
  'regulatory_reporting',
  'data_engine',
  'institution',
  'reports',
  'settings',
]);

export type ModuleScope = {
  /**
   * The scoped module set from the API `default_modules`, or `null` when the bank
   * carries no registry detail. Only meaningful once `isResolved` is true.
   */
  modules: ReadonlySet<ModuleKey> | null;
  institutionClass: string | null;
  /**
   * False while the bank payload is still loading. Until it flips true the scope
   * is UNKNOWN, so nav + data fetches restrict to `CORE_MODULES` rather than
   * assume "everything" — the fix for the every-module-flashes-on-refresh race.
   */
  isResolved: boolean;
};

/** Build a scope from the API `default_modules` list. Empty/absent → null. */
export function moduleSetFrom(defaultModules: readonly string[] | null | undefined): ReadonlySet<ModuleKey> | null {
  if (!defaultModules || defaultModules.length === 0) return null;
  return new Set(defaultModules as ModuleKey[]);
}

function subrouteHidden(path: string, scope: ModuleScope): boolean {
  if (!scope.isResolved) {
    return [...SDI_HIDDEN_SUBROUTES, ...SDI_ONLY_SUBROUTES].some(
      (route) => path === route || path.startsWith(`${route}/`)
    );
  }
  if (scope.institutionClass === 'sdi') {
    return SDI_HIDDEN_SUBROUTES.some((r) => path === r || path.startsWith(`${r}/`));
  }
  return SDI_ONLY_SUBROUTES.some((r) => path === r || path.startsWith(`${r}/`));
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
  const moduleKey = moduleForPath(path);
  if (moduleKey && scope.isResolved && scope.modules && !scope.modules.has(moduleKey)) {
    return false;
  }
  if (subrouteHidden(path, scope)) return false;
  return true;
}

/**
 * Is a nav/command href visible, and should its data be fetched? Used by the
 * SIDEBAR, command palette and the per-module data hooks — so it is RESTRICTIVE
 * until the scope resolves: only the always-present `CORE_MODULES` show/fetch,
 * variable modules wait for the tenant's set. Also hides the bank-only BSD return
 * deep links for an SDI (docs/sdi.md §6.3).
 */
export function isHrefVisible(href: string, scope: ModuleScope): boolean {
  if (scope.institutionClass === 'sdi' && /[?&]code=BSD/i.test(href)) return false;
  const path = normalize(href);
  // Hide class-specific subroutes until the class is known. In particular,
  // Capital is a core module but its Basel and SDI tabs are not interchangeable.
  if (subrouteHidden(path, scope)) return false;
  const moduleKey = moduleForPath(path);
  if (moduleKey) {
    if (!scope.isResolved) return CORE_MODULES.has(moduleKey);
    if (scope.modules && !scope.modules.has(moduleKey)) return false;
  }
  return true;
}
