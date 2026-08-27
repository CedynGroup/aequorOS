import type { QueryClient, QueryKey } from '@tanstack/react-query';

/**
 * Identity dimensions that make a browser cache safe to reuse.
 *
 * `tenantId` prevents cross-organization reuse. `authorityId` includes the
 * signed-in actor and their roles so a role change (or an examiner hand-off)
 * cannot inherit data fetched under a different authorization decision.
 */
export type QueryAuthorityScope = Readonly<{
  tenantId: string;
  authorityId: string;
}>;

export type DashboardSemantic =
  Readonly<{ mode: 'current' }> | Readonly<{ mode: 'period'; periodId: string }>;

export const HEAVY_DASHBOARD_QUERY_POLICY = Object.freeze({
  // Detailed regulatory payloads move only when the cheap live-generation
  // signal moves or an accepted mutation/job explicitly invalidates them.
  refetchInterval: false as const,
});

export const LIVE_SIGNAL_POLL_MS = 20_000;

/** Stable authority identity; token rotation deliberately does not change it. */
export function queryAuthorityScope(
  tenantId: string | null | undefined,
  email: string | null | undefined,
  roles: readonly string[] | null | undefined
): QueryAuthorityScope {
  const normalizedRoles = [...(roles ?? [])].sort().join(',');
  return {
    tenantId: tenantId ?? 'tenant:pending',
    authorityId: `${email?.trim().toLowerCase() || 'actor:pending'}|${normalizedRoles || 'roles:pending'}`,
  };
}

/** Prefix-first keys retain the existing prefix invalidation contract. */
export function scopedQueryKey(
  prefix: string,
  scope: QueryAuthorityScope,
  ...dimensions: readonly unknown[]
): QueryKey {
  return [prefix, scope.tenantId, scope.authorityId, ...dimensions];
}

export function dashboardSemantic(periodId?: string): DashboardSemantic {
  return periodId ? { mode: 'period', periodId } : { mode: 'current' };
}

export function dashboardQueryKey(
  prefix: string,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
  semantic: DashboardSemantic
): QueryKey {
  return scopedQueryKey(
    prefix,
    scope,
    bankId ?? null,
    semantic.mode,
    semantic.mode === 'period' ? semantic.periodId : null
  );
}

/** Prefix used to invalidate one bank without disturbing another authority. */
export function scopedBankPrefix(
  prefix: string,
  scope: QueryAuthorityScope,
  bankId: string | undefined
): QueryKey {
  return scopedQueryKey(prefix, scope, bankId ?? null);
}

/**
 * Stable ±10% jitter. It spreads tenants across the polling window without
 * making tests or a single browser session nondeterministic.
 */
export function jitteredPollInterval(
  baseMs: number,
  resource: string,
  scope: QueryAuthorityScope,
  bankId?: string
): number {
  const seed = `${resource}|${scope.tenantId}|${scope.authorityId}|${bankId ?? ''}`;
  let hash = 2166136261;
  for (let index = 0; index < seed.length; index += 1) {
    hash ^= seed.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  const unit = (hash >>> 0) / 0xffffffff;
  return Math.round(baseMs * (0.9 + unit * 0.2));
}

export function invalidateScopedPrefixes(
  queryClient: QueryClient,
  prefixes: readonly string[],
  scope: QueryAuthorityScope,
  bankId: string | undefined
): Promise<void[]> {
  const matchesScope = (prefix: string, key: QueryKey): boolean => {
    if (key[0] !== prefix) return false;
    const scoped =
      key[1] === scope.tenantId &&
      key[2] === scope.authorityId &&
      key[3] === (bankId ?? null);
    // During the incremental key migration, non-home reads still use
    // [prefix, bankId, …]. The QueryClient itself is authority-scoped,
    // so matching that bank-local legacy shape cannot cross a boundary.
    const bankLocalLegacy = Boolean(bankId) && key[1] === bankId;
    return scoped || bankLocalLegacy;
  };

  return Promise.all(
    prefixes.map(async (prefix) => {
      const filters = {
        predicate: (query: { queryKey: QueryKey }) =>
          matchesScope(prefix, query.queryKey),
      };
      await queryClient.cancelQueries(filters);
      await queryClient.invalidateQueries(filters);
    })
  );
}

const GENERATION_PREFIXES: Partial<Record<string, readonly string[]>> = {
  liquidity: ['liq-dashboard'],
  capital: ['cap-dashboard', 'cap-rwa', 'cap-structure'],
  irr: ['irr-dashboard'],
  fx: ['fx-dashboard'],
  ftp: ['ftp-dashboard'],
  forecast: ['forecast-runs'],
};

/** Refresh detailed reads only for engines whose cheap generation moved. */
export function invalidateGenerationChanges(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
  modules: readonly string[]
): Promise<void[]> {
  const prefixes = new Set<string>();
  for (const liveModule of modules) {
    for (const prefix of GENERATION_PREFIXES[liveModule] ?? []) prefixes.add(prefix);
  }
  // The ladder is module-keyed after the scoped bank prefix. Invalidating the
  // bank prefix remains cheap and avoids stale prior-close deltas.
  if (modules.length > 0) prefixes.add('live-snapshots');
  return invalidateScopedPrefixes(queryClient, [...prefixes], scope, bankId);
}

export type LiveGenerationSignal = Readonly<{
  module: string;
  calculationGeneration: number;
  engineVersion: string;
  computedFromInputHash?: unknown;
  sourceFactPeriodId?: unknown;
}>;

export function generationFingerprint(
  modules: readonly LiveGenerationSignal[]
): ReadonlyMap<string, string> {
  return new Map(
    modules.map((liveModule) => [
      liveModule.module,
      JSON.stringify([
        liveModule.calculationGeneration,
        liveModule.engineVersion,
        liveModule.computedFromInputHash ?? null,
        liveModule.sourceFactPeriodId ?? null,
      ]),
    ])
  );
}

export function changedGenerations(
  previous: ReadonlyMap<string, string>,
  next: ReadonlyMap<string, string>
): string[] {
  return [...next].flatMap(([module, fingerprint]) =>
    previous.get(module) === fingerprint ? [] : [module]
  );
}
