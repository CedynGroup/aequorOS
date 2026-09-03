import assert from 'node:assert/strict';

const BANK_ID = 'BK-SAMP0001';
const PERIOD_ID = 'period-latest';
const HISTORICAL_PERIOD_ID = 'period-historical';
const UPDATED_PERIOD_ID = 'period-updated';

/**
 * Wait for the query layer to CONVERGE on an expected state.
 *
 * Every assertion here is about a settled outcome — a fetch count, an
 * effective period — never about latency, so the deadline only needs to be
 * generous enough that a loaded machine is not mistaken for a regression. It
 * was 1s, which failed on busy CI runners and under local load while the
 * behaviour was correct; a broken implementation never converges and still
 * fails, just later.
 */
const CONVERGENCE_TIMEOUT_MS = Number(
  process.env.QUERY_TEST_TIMEOUT_MS ?? 15_000,
);

async function waitFor(check: () => boolean, message: string): Promise<void> {
  const deadline = Date.now() + CONVERGENCE_TIMEOUT_MS;
  while (!check()) {
    if (Date.now() >= deadline) {
      throw new Error(`${message} (after ${CONVERGENCE_TIMEOUT_MS}ms)`);
    }
    await new Promise((resolve) => setTimeout(resolve, 2));
  }
}

async function main(): Promise<void> {
  (globalThis as { window?: object }).window = {};
  (globalThis as { document?: { cookie: string } }).document = {
    cookie: 'aeq-impersonation-active=1',
  };
  const nativeSetInterval = globalThis.setInterval;
  const nativeSetTimeout = globalThis.setTimeout;
  globalThis.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) =>
    nativeSetInterval(handler, timeout && timeout >= 1_000 ? 30 : timeout, ...args)) as typeof setInterval;
  globalThis.setTimeout = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) =>
    nativeSetTimeout(handler, timeout && timeout >= 1_000 ? 30 : timeout, ...args)) as typeof setTimeout;

  const { default: NodeModule } = await import('node:module');
  const moduleWithLoader = NodeModule as typeof NodeModule & {
    _load: (request: string, parent: unknown, isMain: boolean) => unknown;
  };
  const originalLoad = moduleWithLoader._load;
  let loadedReact: typeof import('react') | undefined;
  let loadedHooks: typeof import('./hooks') | undefined;
  class ApiStub {}
  class ConfigurationStub {
    constructor(_options: unknown) {}
  }
  class ResponseErrorStub extends Error {
    response = new Response('{}');
  }
  const generatedApi = new Proxy(
    {
      Configuration: ConfigurationStub,
      ResponseError: ResponseErrorStub,
    } as Record<string, unknown>,
    {
      get: (target, property: string) => target[property] ?? ApiStub,
    },
  );
  const impersonationClaims = {
    typ: 'impersonation',
    org: 'OR-DEM00001',
    act_operator: 'operator@aequoros.example',
    session_id: 'inspection-session',
    roles: ['examiner'],
    iat: Math.floor(Date.now() / 1_000),
    exp: Math.floor(Date.now() / 1_000) + 900,
  };
  const impersonationToken = [
    Buffer.from(JSON.stringify({ alg: 'HS256', typ: 'JWT' })).toString('base64url'),
    Buffer.from(JSON.stringify(impersonationClaims)).toString('base64url'),
    'signed',
  ].join('.');
  moduleWithLoader._load = (request, parent, isMain) => {
    if (request === '@aequoros/risk-service-api') return generatedApi;
    if (request === 'next/server') {
      return {
        NextResponse: {
          json: (body: unknown, init?: ResponseInit) => Response.json(body, init),
        },
      };
    }
    if (request === 'next/headers') {
      return {
        cookies: async () => ({
          get: (name: string) =>
            name === 'aeq-impersonation' ? { value: impersonationToken } : undefined,
        }),
      };
    }
    if (request === '@/lib/impersonation-cookies') {
      return { IMPERSONATION_COOKIE: 'aeq-impersonation' };
    }
    if (request === 'next-auth/react') {
      return {
        getSession: async () => null,
        useSession: () => ({ data: null, status: 'loading' }),
      };
    }
    if (request === 'next/link') {
      return ({ children, ...props }: { children?: import('react').ReactNode }) =>
        loadedReact!.createElement('a', props, children);
    }
    if (request === 'lucide-react') {
      return new Proxy({}, {
        get: () => (props: object) => loadedReact!.createElement('span', props),
      });
    }
    if (request === '@/lib/api/hooks') return loadedHooks;
    if (request === '@/components/ui/SectionCard') {
      return ({ children }: { children?: import('react').ReactNode }) =>
        loadedReact!.createElement('section', null, children);
    }
    if (request === '@/components/ui/StatusPill') {
      return ({ children }: { children?: import('react').ReactNode }) =>
        loadedReact!.createElement('span', null, children);
    }
    if (request === '@/components/ui/Skeleton') {
      return { SkeletonLine: () => loadedReact!.createElement('span') };
    }
    if (request === '@/lib/api/values') {
      return {
        fmtRelative: () => 'just now',
        shortId: (value: string) => value,
      };
    }
    if (request === '@/components/live/moduleDisplay') {
      return { LIVE_MODULE_LABELS: { capital: 'Capital' } };
    }
    return originalLoad(request, parent, isMain);
  };

  const React = await import('react');
  loadedReact = React;
  const { act, create } = await import('react-test-renderer');
  const { focusManager, useQueryClient } = await import('@tanstack/react-query');
  // Keep accelerated signal polls dormant until the explicit focus/poll checks
  // below so they cannot race the controlled invalidation assertions.
  focusManager.setFocused(false);
  const { GET: getImpersonationStatus } = await import(
    '../../app/api/impersonation/status/route'
  );
  const { default: QueryAuthorityBoundary } = await import('./QueryAuthorityBoundary');
  const {
    useQueryAuthorityScope,
    useResolvedQueryAuthorityScope,
  } = await import('./useQueryScope');
  const hooks = await import('./hooks');
  loadedHooks = hooks;
  const { default: FreshnessStrip } = await import(
    '../../components/reports/FreshnessStrip'
  );
  const ingestion = await import('./ingestion');
  const clients = await import('./client');
  moduleWithLoader._load = originalLoad;

  const statusResponse = await getImpersonationStatus();
  const inspectionStatus = (await statusResponse.json()) as {
    operator: string | null;
    org: string | null;
    token: string | null;
  };
  assert.equal(inspectionStatus.operator, impersonationClaims.act_operator);
  assert.equal(inspectionStatus.org, impersonationClaims.org);
  assert.equal(inspectionStatus.token, impersonationToken);

  let statusRequests = 0;
  let releaseStatus: (() => void) | null = null;
  const statusGate = new Promise<void>((resolve) => {
    releaseStatus = resolve;
  });
  globalThis.fetch = (async () => {
    statusRequests += 1;
    if (statusRequests === 1) throw new TypeError('transient status failure');
    await statusGate;
    return Response.json(inspectionStatus);
  }) as typeof fetch;

  const counts = new Map<string, number>();
  const response = <T,>(name: string, value: T) => async () => {
    counts.set(name, (counts.get(name) ?? 0) + 1);
    await new Promise((resolve) => setTimeout(resolve, 1));
    return value;
  };
  let generation = 7;
  let officialGeneration = 1;
  let currentPeriodId = PERIOD_ID;
  let currentDetailGate: Promise<void> | null = null;
  let releaseInitialSignals: (() => void) | null = null;
  const initialSignalGate = new Promise<void>((resolve) => {
    releaseInitialSignals = resolve;
  });
  const liveSummary = async () => {
    counts.set('live-summary', (counts.get('live-summary') ?? 0) + 1);
    await initialSignalGate;
    return {
      modules: [
        {
          module: 'liquidity',
          calculationGeneration: generation,
          engineVersion: 'live-liquidity-v1',
          computedFromInputHash: `hash-${generation}`,
          sourceFactPeriodId: PERIOD_ID,
        },
      ],
    };
  };
  const mock = (target: object, methods: Record<string, unknown>) =>
    Object.assign(target, methods);

  mock(clients.banksApi, {
    listBanks: response('banks', { banks: [] }),
    listBankReportingPeriods: response('periods', { periods: [] }),
    getBankPeriodFacts: response('facts', {}),
  });
  mock(clients.regulatoryLiquidityApi, {
    // Reads the raw envelope (like IRR/FX) so an HTTP 200 {available:false}
    // renders as a module-unavailable panel instead of a false backend error.
    getLiquidityDashboardRaw: async ({
      reportingPeriodId,
    }: {
      reportingPeriodId?: string;
    }) => {
      const name = reportingPeriodId
        ? `liq-dashboard:${reportingPeriodId}`
        : 'liq-dashboard';
      counts.set(name, (counts.get(name) ?? 0) + 1);
      if (!reportingPeriodId && currentDetailGate) await currentDetailGate;
      const body = { period: { id: reportingPeriodId ?? currentPeriodId }, trend: [] };
      return { raw: new Response('{}'), value: async () => body };
    },
    runAllLiquidityScenarios: response('liq-mutation', {}),
  });
  mock(clients.regulatoryCapitalApi, {
    getCapitalDashboardRaw: async ({
      reportingPeriodId,
    }: {
      reportingPeriodId?: string;
    }) => {
      const name = reportingPeriodId
        ? `cap-dashboard:${reportingPeriodId}`
        : 'cap-dashboard';
      counts.set(name, (counts.get(name) ?? 0) + 1);
      if (!reportingPeriodId && currentDetailGate) await currentDetailGate;
      const body = { period: { id: reportingPeriodId ?? currentPeriodId }, trend: [] };
      return { raw: new Response('{}'), value: async () => body };
    },
  });
  mock(clients.regulatoryIrrApi, {
    getIrrDashboardRaw: async () => {
      counts.set('irr-dashboard', (counts.get('irr-dashboard') ?? 0) + 1);
      return {
        raw: new Response('{}'),
        value: async () => ({}),
      };
    },
  });
  mock(clients.regulatoryFxApi, {
    // FX reads the raw envelope (like IRR) so an HTTP 200 {available:false}
    // renders as a module-unavailable panel instead of a false backend error.
    getFxDashboardRaw: async () => {
      counts.set('fx-dashboard', (counts.get('fx-dashboard') ?? 0) + 1);
      return {
        raw: new Response('{}'),
        value: async () => ({}),
      };
    },
  });
  mock(clients.regulatoryFtpApi, {
    getFtpDashboardRaw: async () => {
      counts.set('ftp-dashboard', (counts.get('ftp-dashboard') ?? 0) + 1);
      return { raw: new Response('{}'), value: async () => ({}) };
    },
  });
  mock(clients.liveEngineApi, {
    getLiveSummary: liveSummary,
    getBankFreshness: async ({
      reportingPeriodId,
    }: {
      reportingPeriodId?: string;
    }) => {
      const name = `freshness:${reportingPeriodId ?? 'current'}`;
      counts.set(name, (counts.get(name) ?? 0) + 1);
      await initialSignalGate;
      return {
        reportingPeriodId: reportingPeriodId ?? null,
        modules: [
          {
            module: 'capital',
            officialRunHash: `official-${officialGeneration}`,
            officialRunAt: new Date(
              `2026-08-27T12:0${officialGeneration}:00Z`,
            ),
          },
        ],
      };
    },
    getBankAlerts: response('alerts', {}),
    refreshBankData: response('refresh-mutation', { jobId: 'pipeline-job' }),
    mintOfficialRun: response('official-run-mutation', { jobId: 'official-run-job' }),
    listLiveSnapshots: async ({ module: liveModule }: { module: string }) => {
      const name = `live-snapshots:${liveModule}`;
      counts.set(name, (counts.get(name) ?? 0) + 1);
      return {};
    },
  });
  mock(clients.notificationsApi, {
    listNotifications: response('notifications', {}),
  });
  mock(clients.jobsApi, {
    getJob: response('job-status', { status: 'succeeded' }),
  });
  mock(ingestion.ingestionApi, {
    activateBankData: response('activation-mutation', {}),
    listIngestionBatches: response('de-batches', {}),
    listBankDataActivations: response('de-activations', {}),
  });

  let queryClient: ReturnType<typeof useQueryClient> | null = null;
  let runLiquidityScenarios: (() => Promise<unknown>) | null = null;
  let refreshBankData: (() => Promise<unknown>) | null = null;
  let mintOfficialRun: (() => Promise<unknown>) | null = null;
  let activateBankData: (() => Promise<unknown>) | null = null;
  let authorityMounts = 0;
  let resolvedAuthority: ReturnType<typeof useQueryAuthorityScope> | null = null;
  let selectRatioPeriod: ((periodId: string) => void) | null = null;
  let effectiveLiquidityPeriod: string | undefined;
  let effectiveCapitalPeriod: string | undefined;

  function AuthorityProbe() {
    const authority = useQueryAuthorityScope();
    React.useEffect(() => {
      authorityMounts += 1;
      resolvedAuthority = authority;
    }, [authority]);
    return null;
  }

  function CommandCenterHookHarness() {
    const [ratioPeriodId, setRatioPeriodId] = React.useState(PERIOD_ID);
    selectRatioPeriod = setRatioPeriodId;
    queryClient = useQueryClient();
    hooks.useBanks();
    hooks.useReportingPeriods(BANK_ID);
    hooks.useBankPeriodFacts(BANK_ID, PERIOD_ID);
    hooks.useBankPeriodFacts(BANK_ID, PERIOD_ID);
    hooks.useLiveSummary(BANK_ID);
    hooks.useLiveSummary(BANK_ID);
    hooks.useBankFreshness(BANK_ID, PERIOD_ID, false);
    hooks.useBankAlerts(BANK_ID);
    hooks.useBankAlerts(BANK_ID);
    hooks.useNotifications();
    hooks.useNotifications();
    hooks.useLiquidityDashboard(BANK_ID);
    hooks.useCapitalDashboard(BANK_ID);
    const effectiveRatios = hooks.useEffectiveRatioDashboards(BANK_ID, ratioPeriodId);
    effectiveLiquidityPeriod = effectiveRatios.liquidity.data?.period.id;
    effectiveCapitalPeriod = effectiveRatios.capital.data?.period.id;
    hooks.useIrrDashboard(BANK_ID);
    hooks.useFxDashboard(BANK_ID);
    hooks.useFtpDashboard(BANK_ID);
    for (const liveModule of [
      'liquidity',
      'capital',
      'irr',
      'fx',
      'ftp',
      'rating',
      'forecast',
    ] as const) {
      hooks.useLiveSnapshots(BANK_ID, liveModule);
    }
    ingestion.useIngestionBatches(BANK_ID);
    ingestion.useDataActivations(BANK_ID);
    const mutation = hooks.useRunAllLiquidityScenarios(BANK_ID);
    runLiquidityScenarios = () =>
      mutation.mutateAsync({ reportingPeriodId: PERIOD_ID });
    const refresh = hooks.useRefreshBankData(BANK_ID);
    refreshBankData = () =>
      refresh.mutateAsync({ asOfDate: '2026-08-27', reason: 'test refresh' });
    const officialRun = hooks.useMintOfficialRun(BANK_ID);
    mintOfficialRun = () =>
      officialRun.mutateAsync({ asOfDate: '2026-08-27', reason: 'test official run' });
    const activation = ingestion.useActivateBankData(BANK_ID);
    activateBankData = () =>
      activation.mutateAsync({ asOfDate: '2026-08-27', runCalculations: true });
    return null;
  }

  function ResolvedInspectionBoundary() {
    const scope = useResolvedQueryAuthorityScope();
    return (
      <QueryAuthorityBoundary scope={scope} fallback={<span>loading</span>}>
        <AuthorityProbe />
        <CommandCenterHookHarness />
      </QueryAuthorityBoundary>
    );
  }

  let renderer: ReturnType<typeof create>;
  await act(async () => {
    renderer = create(<ResolvedInspectionBoundary />);
    await new Promise((resolve) => setTimeout(resolve, 70));
  });
  assert.equal(counts.size, 0, 'unresolved authority must not mount query consumers');
  assert.equal(statusRequests, 2, 'transient inspection status failure must retry');

  await act(async () => {
    releaseStatus!();
  });
  await waitFor(
    () =>
      counts.get('live-summary') === 1 &&
      counts.get('freshness:current') === 1 &&
      counts.get(`freshness:${PERIOD_ID}`) === 1,
    'initial dashboard signals did not start',
  );
  assert.equal(
    counts.get('liq-dashboard'),
    undefined,
    'liquidity detail started before its initial signals settled',
  );
  assert.equal(
    counts.get('cap-dashboard'),
    undefined,
    'capital detail started before its initial signals settled',
  );
  await act(async () => {
    releaseInitialSignals!();
  });
  await waitFor(
    () => [...counts.entries()].filter(([name]) => name !== 'liq-mutation').length === 22,
    'Command Center resources did not settle',
  );
  const initialCounts = new Map(counts);
  assert.equal(authorityMounts, 1, 'inspection authority must resolve exactly once');
  assert.deepEqual(resolvedAuthority, {
    tenantId: impersonationClaims.org,
    authorityId: `operator:${impersonationClaims.act_operator}|examiner`,
  });
  assert.equal(initialCounts.get('liq-dashboard'), 1);
  assert.equal(initialCounts.get('cap-dashboard'), 1);
  assert.equal(initialCounts.get('facts'), 1);
  assert.equal(initialCounts.get('live-summary'), 1);
  assert.equal(
    [...initialCounts.entries()]
      .filter(([name]) => name !== 'liq-mutation')
      .reduce((total, [, count]) => total + count, 0),
    22,
    'real duplicate consumers must collapse without cross-period summary polling',
  );

  await act(async () => {
    selectRatioPeriod!(HISTORICAL_PERIOD_ID);
  });
  await waitFor(
    () =>
      counts.get(`liq-dashboard:${HISTORICAL_PERIOD_ID}`) === 1 &&
      counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) === 1 &&
      effectiveLiquidityPeriod === HISTORICAL_PERIOD_ID &&
      effectiveCapitalPeriod === HISTORICAL_PERIOD_ID,
    'historical ratio dashboards did not retain explicit-period semantics',
  );
  assert.equal(effectiveLiquidityPeriod, HISTORICAL_PERIOD_ID);
  assert.equal(effectiveCapitalPeriod, HISTORICAL_PERIOD_ID);
  assert.equal(
    counts.get('freshness:current'),
    1,
    'current-semantic module reads must share one freshness signal',
  );
  assert.equal(
    counts.get(`freshness:${HISTORICAL_PERIOD_ID}`),
    1,
    'effective-period dashboard reads must share one freshness signal',
  );
  officialGeneration += 1;
  const beforeFallbackOfficialCapital =
    counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) ?? 0;
  await act(async () => {
    await queryClient!.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === 'freshness' &&
        query.queryKey[4] === HISTORICAL_PERIOD_ID,
    });
  });
  await waitFor(
    () =>
      counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) ===
      beforeFallbackOfficialCapital + 1,
    'effective-period official-run signal did not invalidate displayed detail',
  );

  let releaseHistoricalRefetch: (() => void) | null = null;
  currentDetailGate = new Promise<void>((resolve) => {
    releaseHistoricalRefetch = resolve;
  });
  const beforeHistoricalRefetch = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    void queryClient!.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === 'liq-dashboard' && query.queryKey[4] === 'current',
    });
    void queryClient!.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === 'cap-dashboard' && query.queryKey[4] === 'current',
    });
  });
  await waitFor(
    () => counts.get('liq-dashboard') === beforeHistoricalRefetch + 1,
    'current dashboard did not begin its historical-selection refetch',
  );
  assert.equal(effectiveLiquidityPeriod, HISTORICAL_PERIOD_ID);
  assert.equal(effectiveCapitalPeriod, HISTORICAL_PERIOD_ID);
  currentDetailGate = null;
  await act(async () => {
    releaseHistoricalRefetch!();
  });

  let releaseCurrentDetail: (() => void) | null = null;
  currentDetailGate = new Promise<void>((resolve) => {
    releaseCurrentDetail = resolve;
  });
  const beforeCurrentRefetch = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    void queryClient!.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === 'liq-dashboard' && query.queryKey[4] === 'current',
    });
    void queryClient!.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === 'cap-dashboard' && query.queryKey[4] === 'current',
    });
    selectRatioPeriod!(UPDATED_PERIOD_ID);
  });
  await waitFor(
    () => counts.get('liq-dashboard') === beforeCurrentRefetch + 1,
    'current ratio dashboard did not begin refetching',
  );
  await waitFor(
    () =>
      counts.get(`liq-dashboard:${UPDATED_PERIOD_ID}`) === 1 &&
      counts.get(`cap-dashboard:${UPDATED_PERIOD_ID}`) === 1 &&
      effectiveLiquidityPeriod === UPDATED_PERIOD_ID &&
      effectiveCapitalPeriod === UPDATED_PERIOD_ID,
    'a new period selection must use its own fallback during a current refetch',
  );
  currentPeriodId = UPDATED_PERIOD_ID;
  currentDetailGate = null;
  await act(async () => {
    releaseCurrentDetail!();
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(counts.get(`liq-dashboard:${UPDATED_PERIOD_ID}`), 1);
  assert.equal(counts.get(`cap-dashboard:${UPDATED_PERIOD_ID}`), 1);
  assert.equal(effectiveLiquidityPeriod, UPDATED_PERIOD_ID);
  assert.equal(effectiveCapitalPeriod, UPDATED_PERIOD_ID);

  const beforeIdleLiquidity = counts.get('liq-dashboard') ?? 0;
  const beforeIdleCapital = counts.get('cap-dashboard') ?? 0;
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 70));
  });
  assert.equal(counts.get('liq-dashboard'), beforeIdleLiquidity);
  assert.equal(counts.get('cap-dashboard'), beforeIdleCapital);
  const idleCapitalCount = counts.get('cap-dashboard') ?? 0;

  officialGeneration += 1;
  const beforeScheduledOfficialCapital = counts.get('cap-dashboard') ?? 0;
  const beforeScheduledOfficialLiquidity = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await queryClient!.invalidateQueries({
      predicate: (query) =>
        query.queryKey[0] === 'freshness' && query.queryKey[4] === null,
    });
  });
  await waitFor(
    () => counts.get('cap-dashboard') === beforeScheduledOfficialCapital + 1,
    'scheduled official-run signal did not invalidate capital detail',
  );
  assert.equal(
    counts.get('liq-dashboard'),
    beforeScheduledOfficialLiquidity,
    'official-run signal invalidated an unaffected module',
  );

  const beforeFocusLiquidity = counts.get('liq-dashboard') ?? 0;
  const beforeFocusCapital = counts.get('cap-dashboard') ?? 0;
  focusManager.setFocused(false);
  await act(async () => {
    focusManager.setFocused(true);
  });
  await waitFor(
    () =>
      counts.get('liq-dashboard') === beforeFocusLiquidity + 1 &&
      counts.get('cap-dashboard') === beforeFocusCapital + 1,
    'returning focus did not revalidate full-range trend details',
  );

  const beforeLiquidityMutation = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await runLiquidityScenarios!();
  });
  await waitFor(
    () => counts.get('liq-dashboard') === beforeLiquidityMutation + 1,
    'mutation did not invalidate detail',
  );

  generation += 1;
  const beforePipelineRefresh = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await refreshBankData!();
  });
  await waitFor(
    () => counts.get('liq-dashboard') === beforePipelineRefresh + 1,
    'pipeline generation did not invalidate detail',
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(
    counts.get('liq-dashboard'),
    beforePipelineRefresh + 1,
    'pipeline completion must fetch generation-owned detail exactly once',
  );

  generation += 1;
  const beforeSignalRefresh = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await queryClient!.invalidateQueries({ queryKey: ['live-summary'] });
  });
  await waitFor(
    () => counts.get('liq-dashboard') === beforeSignalRefresh + 1,
    'live generation change did not invalidate detail',
  );

  generation += 1;
  const beforeActivationLiquidity = counts.get('liq-dashboard') ?? 0;
  const beforeActivationCapital = counts.get('cap-dashboard') ?? 0;
  await act(async () => {
    await activateBankData!();
  });
  await waitFor(
    () =>
      counts.get('liq-dashboard') === beforeActivationLiquidity + 1 &&
      counts.get('cap-dashboard') === beforeActivationCapital + 1,
    'activation did not refresh detailed dashboards',
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(counts.get('liq-dashboard'), beforeActivationLiquidity + 1);
  assert.equal(counts.get('cap-dashboard'), beforeActivationCapital + 1);

  await act(async () => {
    selectRatioPeriod!(HISTORICAL_PERIOD_ID);
  });
  await waitFor(
    () =>
      (counts.get(`liq-dashboard:${HISTORICAL_PERIOD_ID}`) ?? 0) >= 2 &&
      (counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) ?? 0) >= 2 &&
      (counts.get(`freshness:${HISTORICAL_PERIOD_ID}`) ?? 0) >= 2,
    'historical dashboards did not reactivate before the official run',
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  const beforeOfficialLiquidity = counts.get('liq-dashboard') ?? 0;
  const beforeOfficialCapital = counts.get('cap-dashboard') ?? 0;
  const beforeOfficialHistoricalLiquidity =
    counts.get(`liq-dashboard:${HISTORICAL_PERIOD_ID}`) ?? 0;
  const beforeOfficialHistoricalCapital =
    counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) ?? 0;
  await act(async () => {
    await mintOfficialRun!();
  });
  await waitFor(
    () =>
      counts.get('liq-dashboard') === beforeOfficialLiquidity + 1 &&
      counts.get('cap-dashboard') === beforeOfficialCapital + 1 &&
      counts.get(`liq-dashboard:${HISTORICAL_PERIOD_ID}`) ===
        beforeOfficialHistoricalLiquidity + 1 &&
      counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) ===
        beforeOfficialHistoricalCapital + 1,
    'official run did not refresh regulatory detail',
  );
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(counts.get('liq-dashboard'), beforeOfficialLiquidity + 1);
  assert.equal(counts.get('cap-dashboard'), beforeOfficialCapital + 1);
  assert.equal(
    counts.get(`liq-dashboard:${HISTORICAL_PERIOD_ID}`),
    beforeOfficialHistoricalLiquidity + 1,
  );
  assert.equal(
    counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`),
    beforeOfficialHistoricalCapital + 1,
  );
  assert.ok((counts.get('cap-dashboard') ?? 0) > idleCapitalCount);

  await act(async () => renderer!.unmount());

  const reportsPeriodId = 'period-reports';
  const beforeReportsFreshness = counts.get(`freshness:${reportsPeriodId}`) ?? 0;
  let reportsRenderer: ReturnType<typeof create>;
  await act(async () => {
    reportsRenderer = create(
      <QueryAuthorityBoundary
        scope={{
          tenantId: impersonationClaims.org,
          authorityId: `operator:${impersonationClaims.act_operator}|examiner`,
        }}
      >
        <FreshnessStrip
          bankId={BANK_ID}
          period={{ id: reportsPeriodId, label: 'Reports period' } as never}
        />
      </QueryAuthorityBoundary>,
    );
    await new Promise((resolve) => setTimeout(resolve, 70));
  });
  assert.ok(
    (counts.get(`freshness:${reportsPeriodId}`) ?? 0) >=
      beforeReportsFreshness + 2,
    'Reports freshness strip must retain the cheap jittered poll',
  );
  await act(async () => reportsRenderer!.unmount());
  globalThis.setInterval = nativeSetInterval;
  globalThis.setTimeout = nativeSetTimeout;
  console.log(
    'queryAuthorityBoundary.test.tsx: pending 0; settled 22 resources/calls; focus, idle, and invalidation passed',
  );
}

void main().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
