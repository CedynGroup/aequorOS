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

export async function waitForInitialDashboardSignals(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined
): Promise<void> {
  const signals = queryClient.getQueryCache().findAll({
    predicate: (query) => {
      const key = query.queryKey;
      return (
        (key[0] === 'live-summary' ||
          key[0] === 'freshness' ||
          key[0] === 'official-run-signal') &&
        key[1] === scope.tenantId &&
        key[2] === scope.authorityId &&
        key[3] === (bankId ?? null)
      );
    },
  });
  await Promise.allSettled(
    signals.flatMap((query) => {
      if (query.state.status === 'error') {
        return [query.fetch()];
      }
      if (query.promise) return [query.promise];
      if (query.state.status === 'pending') return [query.fetch()];
      return [];
    })
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
  return invalidateMatchingScopedPrefixes(
    queryClient,
    prefixes,
    scope,
    bankId,
    false,
  );
}

export function invalidateCachedScopedPrefixes(
  queryClient: QueryClient,
  prefixes: readonly string[],
  scope: QueryAuthorityScope,
  bankId: string | undefined
): Promise<void[]> {
  return invalidateMatchingScopedPrefixes(
    queryClient,
    prefixes,
    scope,
    bankId,
    true,
  );
}

export function reconcileStartedScopedPrefixes(
  queryClient: QueryClient,
  prefixes: readonly string[],
  scope: QueryAuthorityScope,
  bankId: string | undefined
): Promise<void[]> {
  return invalidateMatchingScopedPrefixes(
    queryClient,
    prefixes,
    scope,
    bankId,
    'started',
  );
}

function invalidateMatchingScopedPrefixes(
  queryClient: QueryClient,
  prefixes: readonly string[],
  scope: QueryAuthorityScope,
  bankId: string | undefined,
  selection: boolean | 'started',
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
      type Candidate = {
        queryKey: QueryKey;
        state: { data: unknown; fetchStatus: string };
      };
      const selected = selection === 'started'
        ? new Set<object>(
            queryClient.getQueryCache().findAll({
              predicate: (query: Candidate) =>
                matchesScope(prefix, query.queryKey) &&
                (query.state.data !== undefined ||
                  query.state.fetchStatus === 'fetching'),
            })
          )
        : undefined;
      const filters = {
        predicate: (query: Candidate) =>
          selected
            ? selected.has(query)
            : matchesScope(prefix, query.queryKey) &&
              (selection === false || query.state.data !== undefined),
      };
      await queryClient.cancelQueries(filters);
      await queryClient.invalidateQueries(filters);
    })
  );
}

const REGULATORY_DETAIL_PREFIXES: Partial<Record<string, readonly string[]>> = {
  liquidity: ['liq-dashboard'],
  capital: ['cap-dashboard', 'cap-rwa', 'cap-structure'],
  irr: ['irr-dashboard'],
  fx: ['fx-dashboard'],
  ftp: ['ftp-dashboard'],
  forecast: ['forecast-runs'],
};

export function regulatoryDetailInvalidationPrefixes(
  modules: readonly string[]
): string[] {
  const prefixes = new Set<string>();
  for (const liveModule of modules) {
    for (const prefix of REGULATORY_DETAIL_PREFIXES[liveModule] ?? []) {
      prefixes.add(prefix);
    }
  }
  return [...prefixes];
}

export function generationInvalidationPrefixes(
  modules: readonly string[]
): string[] {
  const prefixes = new Set(regulatoryDetailInvalidationPrefixes(modules));
  if (modules.length > 0) prefixes.add('live-snapshots');
  return [...prefixes];
}

/** Refresh detailed reads only for engines whose cheap generation moved. */
export function invalidateGenerationChanges(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
  modules: readonly string[]
): Promise<void[]> {
  return invalidateScopedPrefixes(
    queryClient,
    generationInvalidationPrefixes(modules),
    scope,
    bankId
  );
}

export type OfficialRunSignal = Readonly<{
  module: string;
  officialRunHash?: unknown;
  officialRunAt?: unknown;
}>;

export function officialRunFingerprint(
  modules: readonly OfficialRunSignal[]
): ReadonlyMap<string, string> {
  return new Map(
    modules.map((liveModule) => [
      liveModule.module,
      JSON.stringify([
        liveModule.officialRunHash ?? null,
        liveModule.officialRunAt ?? null,
      ]),
    ])
  );
}

export function invalidateOfficialRunChanges(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
  modules: readonly string[]
): Promise<void[]> {
  return invalidateScopedPrefixes(
    queryClient,
    regulatoryDetailInvalidationPrefixes(modules),
    scope,
    bankId
  );
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

export function observedSignalChanges(
  previous: ReadonlyMap<string, string> | undefined,
  next: ReadonlyMap<string, string>,
  reconcileAfterError: boolean
): string[] {
  if (previous) return changedGenerations(previous, next);
  return reconcileAfterError ? [...next.keys()] : [];
}

export type OfficialCompletionSignal = Readonly<{
  id: string;
  module: string;
  scenarioCode: string;
  status: string;
  inputHash: string;
  evidence?: Readonly<{
    status: string;
    rowsWithdrawn?: number;
    withdrawals?: readonly Readonly<{
      withdrawalId: string;
      approvedAt?: unknown;
    }>[];
  }>;
}>;

const OFFICIAL_COMPLETION_SCENARIOS = new Map([
  ['liquidity', 'baseline'],
  ['capital', 'baseline'],
  ['irr', 'baseline'],
  ['fx', 'baseline'],
  ['ftp', 'baseline'],
  ['forecast', 'base'],
]);

export function officialCompletionFingerprint(
  runs: readonly OfficialCompletionSignal[]
): ReadonlyMap<string, string> {
  const moduleRuns = new Map<string, string[]>();
  for (const run of runs) {
    const officialScenario = OFFICIAL_COMPLETION_SCENARIOS.get(run.module);
    if (
      !officialScenario ||
      run.status !== 'succeeded' ||
      run.scenarioCode !== officialScenario
    ) {
      continue;
    }
    const fingerprints = moduleRuns.get(run.module) ?? [];
    fingerprints.push(
      JSON.stringify([
        run.id,
        run.inputHash,
        run.evidence?.status ?? null,
        run.evidence?.rowsWithdrawn ?? null,
        [...(run.evidence?.withdrawals ?? [])]
          .map((withdrawal) => [
            withdrawal.withdrawalId,
            withdrawal.approvedAt ?? null,
          ])
          .sort(([left], [right]) => String(left).localeCompare(String(right))),
      ])
    );
    moduleRuns.set(run.module, fingerprints);
  }
  return new Map(
    [...moduleRuns].map(([module, fingerprints]) => [
      module,
      JSON.stringify(fingerprints.sort()),
    ])
  );
}

export type OfficialPeriodSignal = Readonly<{
  reportingPeriodId: string | null;
  modules: readonly OfficialRunSignal[];
}>;

export function officialBaselineFingerprint(
  periods: readonly OfficialPeriodSignal[],
  runs: readonly OfficialCompletionSignal[],
  previous?: ReadonlyMap<string, string>
): ReadonlyMap<string, string> {
  const periodFingerprints = new Map<string, string[]>();
  for (const period of periods) {
    for (const moduleSignal of period.modules) {
      const fingerprints = periodFingerprints.get(moduleSignal.module) ?? [];
      fingerprints.push(
        JSON.stringify([
          period.reportingPeriodId,
          moduleSignal.officialRunHash ?? null,
          moduleSignal.officialRunAt ?? null,
        ])
      );
      periodFingerprints.set(moduleSignal.module, fingerprints);
    }
  }

  const completionFingerprints = officialCompletionFingerprint(runs);
  const modules = new Set([
    ...periodFingerprints.keys(),
    ...completionFingerprints.keys(),
  ]);
  return new Map(
    [...modules].map((module) => {
      let completionFingerprint = completionFingerprints.get(module);
      if (completionFingerprint === undefined) {
        const previousFingerprint = previous?.get(module);
        if (previousFingerprint) {
          const parsed = JSON.parse(previousFingerprint) as [unknown, unknown];
          completionFingerprint =
            typeof parsed[1] === 'string' ? parsed[1] : undefined;
        }
      }
      return [
        module,
        JSON.stringify([
          [...(periodFingerprints.get(module) ?? [])].sort(),
          completionFingerprint ?? null,
        ]),
      ];
    })
  );
}

export async function refreshLiveSummaryGenerationChanges(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined
): Promise<string[]> {
  const queryKey = scopedQueryKey('live-summary', scope, bankId ?? null);
  const before = queryClient.getQueryData<{ modules?: readonly LiveGenerationSignal[] }>(
    queryKey
  );
  await invalidateScopedPrefixes(queryClient, ['live-summary'], scope, bankId);
  const after = queryClient.getQueryData<{ modules?: readonly LiveGenerationSignal[] }>(
    queryKey
  );
  if (!before?.modules || !after?.modules) return [];
  return changedGenerations(
    generationFingerprint(before.modules),
    generationFingerprint(after.modules)
  );
}
