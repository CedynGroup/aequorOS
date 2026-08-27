/**
 * Deterministic Command Center request/cache fixture.
 *
 * The bounded-idle checks scale 30 seconds to 30 milliseconds; they exercise
 * TanStack Query observers in browser mode without a backend, database, or
 * worker. Run: pnpm --filter @aequoros/dashboard test
 */

import assert from 'node:assert/strict';
import {
  HEAVY_DASHBOARD_QUERY_POLICY,
  LIVE_SIGNAL_POLL_MS,
  changedGenerations,
  dashboardQueryKey,
  dashboardSemantic,
  generationFingerprint,
  invalidateGenerationChanges,
  invalidateScopedPrefixes,
  jitteredPollInterval,
  queryAuthorityScope,
  scopedQueryKey,
} from './queryPolicy';

const BANK_ID = 'BK-SAMP0001';
const PERIOD_ID = 'period-latest';
const scope = queryAuthorityScope('OR-DEM00001', 'analyst@aequoros.example', ['analyst']);

async function waitFor(check: () => boolean, message: string): Promise<void> {
  const deadline = Date.now() + 500;
  while (!check()) {
    if (Date.now() >= deadline) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

async function main(): Promise<void> {
  // Force TanStack's browser scheduler before loading it. Server-mode Query
  // observers intentionally suppress intervals and would make an idle-window
  // regression test pass without exercising the browser behavior.
  (globalThis as { window?: object }).window = {};
  const { QueryClient, QueryObserver, focusManager } = await import('@tanstack/react-query');
  focusManager.setFocused(true);

  const currentLiquidity = dashboardQueryKey('liq-dashboard', scope, BANK_ID, dashboardSemantic());
  const selectedLiquidity = dashboardQueryKey(
    'liq-dashboard',
    scope,
    BANK_ID,
    dashboardSemantic(PERIOD_ID)
  );
  assert.notDeepEqual(
    currentLiquidity,
    selectedLiquidity,
    'current and selected-period semantics must retain explicit stable keys'
  );
  assert.deepEqual(currentLiquidity.slice(0, 4), [
    'liq-dashboard',
    scope.tenantId,
    scope.authorityId,
    BANK_ID,
  ]);
  assert.equal(currentLiquidity.at(-2), 'current');
  assert.equal(selectedLiquidity.at(-1), PERIOD_ID);

  const otherTenant = queryAuthorityScope('OR-OTHER001', 'analyst@aequoros.example', ['analyst']);
  const otherAuthority = queryAuthorityScope(scope.tenantId, 'examiner@aequoros.example', [
    'examiner',
  ]);
  const tenantKey = dashboardQueryKey('liq-dashboard', otherTenant, BANK_ID, dashboardSemantic());
  const authorityKey = dashboardQueryKey(
    'liq-dashboard',
    otherAuthority,
    BANK_ID,
    dashboardSemantic()
  );
  assert.notDeepEqual(currentLiquidity, tenantKey);
  assert.notDeepEqual(currentLiquidity, authorityKey);

  // The settled home owns 20 logical resources. Duplicate consumers (header,
  // breach banner, pulse wall, ratio panel, balance strip) all ask TanStack for
  // the same stable keys, yielding one request per resource.
  const current = (prefix: string) =>
    dashboardQueryKey(prefix, scope, BANK_ID, dashboardSemantic());
  const homeResources = [
    scopedQueryKey('banks', scope),
    scopedQueryKey('periods', scope, BANK_ID),
    scopedQueryKey('facts', scope, BANK_ID, PERIOD_ID),
    scopedQueryKey('live-summary', scope, BANK_ID),
    scopedQueryKey('alerts', scope, BANK_ID, 20),
    scopedQueryKey('notifications', scope, false),
    current('liq-dashboard'),
    current('cap-dashboard'),
    current('irr-dashboard'),
    current('fx-dashboard'),
    current('ftp-dashboard'),
    ...['liquidity', 'capital', 'irr', 'fx', 'ftp', 'rating', 'forecast'].map((module) =>
      scopedQueryKey('live-snapshots', scope, BANK_ID, module, 45)
    ),
    scopedQueryKey('de-batches', scope, BANK_ID, 'all'),
    scopedQueryKey('de-activations', scope, BANK_ID),
  ];
  assert.equal(homeResources.length, 20);
  const requestCounts = new Map<string, number>();
  const requestClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 1_000 } },
  });
  await Promise.all(
    homeResources.flatMap((queryKey) => {
      const name = JSON.stringify(queryKey);
      const request = () =>
        requestClient.fetchQuery({
          queryKey,
          queryFn: async () => {
            requestCounts.set(name, (requestCounts.get(name) ?? 0) + 1);
            await new Promise((resolve) => setTimeout(resolve, 1));
            return name;
          },
        });
      return [request(), request()];
    })
  );
  assert.equal(requestCounts.size, homeResources.length);
  assert.equal(
    [...requestCounts.values()].reduce((sum, count) => sum + count, 0),
    homeResources.length,
    'every logical home resource should issue exactly one request'
  );

  // The ratio panel and pulse wall now share the current semantic key. An
  // explicit historical read still remains a separate cache entry.
  requestClient.removeQueries({ queryKey: currentLiquidity, exact: true });
  let equivalentPayloads = 0;
  await Promise.all([
    requestClient.fetchQuery({
      queryKey: currentLiquidity,
      queryFn: async () => ++equivalentPayloads,
    }),
    requestClient.fetchQuery({
      queryKey: currentLiquidity,
      queryFn: async () => ++equivalentPayloads,
    }),
  ]);
  assert.equal(equivalentPayloads, 1);
  await requestClient.fetchQuery({
    queryKey: selectedLiquidity,
    queryFn: async () => ++equivalentPayloads,
  });
  assert.equal(equivalentPayloads, 2, 'historical semantics remain distinct');

  // A role/tenant boundary is a cache miss even for the same bank id.
  requestClient.setQueryData(currentLiquidity, { confidential: true });
  assert.equal(requestClient.getQueryData(tenantKey), undefined);
  assert.equal(requestClient.getQueryData(authorityKey), undefined);

  // Browser-mode bounded idle window: the detailed observer fetches once and
  // never enters a fixed cadence. If the policy regresses to any interval,
  // scale it to 30ms so this test observes at least two subsequent ticks.
  const idleClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  let idleRequests = 0;
  const detailedObserver = new QueryObserver(idleClient, {
    queryKey: currentLiquidity,
    queryFn: async () => ++idleRequests,
    refetchInterval: HEAVY_DASHBOARD_QUERY_POLICY.refetchInterval === false ? false : 30,
    refetchIntervalInBackground: true,
  });
  const unsubscribeDetailed = detailedObserver.subscribe(() => undefined);
  await waitFor(() => idleRequests === 1, 'detailed dashboard did not load');
  await new Promise((resolve) => setTimeout(resolve, 70));
  assert.equal(idleRequests, 1, 'heavyweight detail must not poll during the bounded idle window');

  // Accepted mutations invalidate the scoped active query exactly once.
  await invalidateScopedPrefixes(idleClient, ['liq-dashboard'], scope, BANK_ID);
  await waitFor(() => idleRequests === 2, 'mutation invalidation did not refetch');

  // A generation change from the cheap signal refreshes the affected detail;
  // an unchanged generation is a no-op.
  const first = generationFingerprint([
    {
      module: 'liquidity',
      calculationGeneration: 7,
      engineVersion: 'live-liquidity-v1',
      computedFromInputHash: 'hash-a',
      sourceFactPeriodId: PERIOD_ID,
    },
  ]);
  const unchanged = generationFingerprint([
    {
      module: 'liquidity',
      calculationGeneration: 7,
      engineVersion: 'live-liquidity-v1',
      computedFromInputHash: 'hash-a',
      sourceFactPeriodId: PERIOD_ID,
    },
  ]);
  assert.deepEqual(changedGenerations(first, unchanged), []);
  const next = generationFingerprint([
    {
      module: 'liquidity',
      calculationGeneration: 8,
      engineVersion: 'live-liquidity-v1',
      computedFromInputHash: 'hash-b',
      sourceFactPeriodId: PERIOD_ID,
    },
  ]);
  const changed = changedGenerations(first, next);
  assert.deepEqual(changed, ['liquidity']);
  await invalidateGenerationChanges(idleClient, scope, BANK_ID, changed);
  await waitFor(() => idleRequests === 3, 'generation invalidation did not refetch');

  const raceClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 } },
  });
  const releases: Array<(value: string) => void> = [];
  let raceRequests = 0;
  const raceObserver = new QueryObserver(raceClient, {
    queryKey: currentLiquidity,
    queryFn: () =>
      new Promise<string>((resolve) => {
        raceRequests += 1;
        releases.push(resolve);
      }),
  });
  const unsubscribeRace = raceObserver.subscribe(() => undefined);
  await waitFor(() => raceRequests === 1, 'initial race request did not start');
  const invalidation = invalidateGenerationChanges(
    raceClient,
    scope,
    BANK_ID,
    ['liquidity'],
  );
  await waitFor(
    () => raceRequests === 2,
    'generation invalidation did not replace the in-flight request',
  );
  releases[0]('stale-generation');
  releases[1]('fresh-generation');
  await invalidation;
  await waitFor(
    () => raceClient.getQueryData(currentLiquidity) === 'fresh-generation',
    'superseded in-flight response remained cached',
  );

  const jitter = jitteredPollInterval(LIVE_SIGNAL_POLL_MS, 'live-summary', scope, BANK_ID);
  assert.equal(jitter, jitteredPollInterval(LIVE_SIGNAL_POLL_MS, 'live-summary', scope, BANK_ID));
  assert.ok(jitter >= 18_000 && jitter <= 22_000);

  // Count model for the same 65-second idle window used by the pre-change
  // fixture. Only the cheap live summary, alert list, and inbox retain a
  // cadence. With this fixture's deterministic jitter: 20 initial resources
  // + 3 summary ticks + 3 alert ticks + 1 inbox tick = 27 requests. The five
  // module detail payloads each load once and contribute zero idle polls.
  const idleWindowMs = 65_000;
  const retainedIntervals = [
    jitter,
    jitteredPollInterval(LIVE_SIGNAL_POLL_MS, 'alerts', scope, BANK_ID),
    jitteredPollInterval(60_000, 'notifications', scope),
  ];
  const afterIdleRequests =
    homeResources.length +
    retainedIntervals.reduce(
      (ticks, interval) => ticks + Math.floor(idleWindowMs / interval),
      0
    );
  assert.equal(afterIdleRequests, 27);
  assert.equal(
    homeResources.filter((key) =>
      [
        'liq-dashboard',
        'cap-dashboard',
        'irr-dashboard',
        'fx-dashboard',
        'ftp-dashboard',
      ].includes(String(key[0]))
    ).length,
    5
  );

  unsubscribeDetailed();
  detailedObserver.destroy();
  unsubscribeRace();
  raceObserver.destroy();
  raceClient.clear();
  idleClient.clear();
  requestClient.clear();
  console.log(
    'queryPolicy.test.ts: before 23 resources/44 calls/21 detail calls -> after 20/27/5; detailed idle polls 0; invalidation passed'
  );
}

void main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
