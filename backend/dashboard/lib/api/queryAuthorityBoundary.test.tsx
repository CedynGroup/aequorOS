import assert from 'node:assert/strict';

const BANK_ID = 'BK-SAMP0001';
const PERIOD_ID = 'period-latest';
const HISTORICAL_PERIOD_ID = 'period-historical';
const TREND_PERIOD_IDS = Array.from(
  { length: 12 },
  (_, index) => `period-trend-${index + 1}`,
);
const UPDATED_PERIOD_ID = 'period-updated';

async function waitFor(check: () => boolean, message: string): Promise<void> {
  const deadline = Date.now() + 1_000;
  while (!check()) {
    if (Date.now() >= deadline) throw new Error(message);
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
    return originalLoad(request, parent, isMain);
  };

  const React = await import('react');
  const { act, create } = await import('react-test-renderer');
  const { useQueryClient } = await import('@tanstack/react-query');
  const { GET: getImpersonationStatus } = await import(
    '../../app/api/impersonation/status/route'
  );
  const { default: QueryAuthorityBoundary } = await import('./QueryAuthorityBoundary');
  const {
    useQueryAuthorityScope,
    useResolvedQueryAuthorityScope,
  } = await import('./useQueryScope');
  const hooks = await import('./hooks');
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
  let trendOfficialGeneration = 1;
  type MockRegulatoryRun = {
    id: string;
    module: string;
    scenarioCode: string;
    status: string;
    inputHash: string;
    evidence?: {
      status: string;
      rowsWithdrawn: number;
      withdrawals: Array<{ withdrawalId: string; approvedAt: string }>;
    };
  };
  const latestRegulatoryRuns: MockRegulatoryRun[] = [
    {
      id: 'liquidity-official-1',
      module: 'liquidity',
      scenarioCode: 'baseline',
      status: 'succeeded',
      inputHash: 'liquidity-official-hash-1',
    },
  ];
  const officialSignalRequests: Array<{
    module?: string;
    scenarioCode?: string;
    limit: number;
    offset: number;
  }> = [];
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
    getLiquidityDashboard: async ({ reportingPeriodId }: { reportingPeriodId?: string }) => {
      const name = reportingPeriodId
        ? `liq-dashboard:${reportingPeriodId}`
        : 'liq-dashboard';
      counts.set(name, (counts.get(name) ?? 0) + 1);
      if (!reportingPeriodId && currentDetailGate) await currentDetailGate;
      return { period: { id: reportingPeriodId ?? currentPeriodId }, trend: [] };
    },
    runAllLiquidityScenarios: response('liq-mutation', {}),
    listRegulatoryRuns: async ({
      module,
      scenarioCode,
      limit = 25,
      offset = 0,
    }: {
      module?: string;
      scenarioCode?: string;
      limit?: number;
      offset?: number;
    }) => {
      officialSignalRequests.push({ module, scenarioCode, limit, offset });
      counts.set(
        'official-run-signal',
        (counts.get('official-run-signal') ?? 0) + 1,
      );
      await initialSignalGate;
      const matching = latestRegulatoryRuns.filter(
        (run) =>
          (!module || run.module === module) &&
          (!scenarioCode || run.scenarioCode === scenarioCode),
      );
      const runs = matching.slice(offset, offset + limit);
      return {
        total: matching.length,
        runs,
        hasMore: offset + runs.length < matching.length,
      };
    },
  });
  mock(clients.regulatoryCapitalApi, {
    getCapitalDashboard: async ({ reportingPeriodId }: { reportingPeriodId?: string }) => {
      const name = reportingPeriodId
        ? `cap-dashboard:${reportingPeriodId}`
        : 'cap-dashboard';
      counts.set(name, (counts.get(name) ?? 0) + 1);
      if (!reportingPeriodId && currentDetailGate) await currentDetailGate;
      return { period: { id: reportingPeriodId ?? currentPeriodId }, trend: [] };
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
    getFxDashboard: response('fx-dashboard', {}),
  });
  mock(clients.regulatoryFtpApi, {
    getFtpDashboard: response('ftp-dashboard', {}),
  });
  mock(clients.liveEngineApi, {
    getLiveSummary: liveSummary,
    getBankFreshness: async ({
      reportingPeriodId,
    }: {
      reportingPeriodId?: string;
    }) => {
      const name =
        reportingPeriodId === PERIOD_ID
          ? 'freshness'
          : 'official-period-signal';
      const observedOfficialGeneration =
        reportingPeriodId === PERIOD_ID
          ? officialGeneration
          : trendOfficialGeneration;
      counts.set(name, (counts.get(name) ?? 0) + 1);
      await initialSignalGate;
      return {
        reportingPeriodId: reportingPeriodId ?? null,
        modules: [
          {
            module: 'capital',
            officialRunHash: `official-${observedOfficialGeneration}`,
            officialRunAt: new Date(
              `2026-08-27T12:0${observedOfficialGeneration}:00Z`,
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
    hooks.useLatestOfficialRunSignal(BANK_ID, TREND_PERIOD_IDS);
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
      counts.get('freshness') === 1 &&
      counts.get('official-period-signal') === TREND_PERIOD_IDS.length &&
      counts.get('official-run-signal') === 2,
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
    () => [...counts.entries()].filter(([name]) => name !== 'liq-mutation').length === 23,
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
    35,
    'real duplicate consumers must collapse while the bounded trend signal reads two modules',
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
      predicate: (query) => query.queryKey[0] === 'freshness',
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

  latestRegulatoryRuns.unshift({
    id: 'capital-stress-2',
    module: 'capital',
    scenarioCode: 'severe',
    status: 'succeeded',
    inputHash: 'capital-stress-hash-2',
  });
  const beforeScenarioCapital = counts.get('cap-dashboard') ?? 0;
  const beforeScenarioLiquidity = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await queryClient!.invalidateQueries({
      predicate: (query) => query.queryKey[0] === 'official-run-signal',
    });
  });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(counts.get('cap-dashboard'), beforeScenarioCapital);
  assert.equal(counts.get('liq-dashboard'), beforeScenarioLiquidity);

  const newerFailedAttempts = Array.from({ length: 101 }, (_, index) => ({
    id: `capital-failed-${index}`,
    module: 'capital',
    scenarioCode: 'baseline',
    status: 'failed',
    inputHash: `capital-failed-hash-${index}`,
  }));
  latestRegulatoryRuns.unshift(
    ...newerFailedAttempts,
    {
      id: 'capital-official-2',
      module: 'capital',
      scenarioCode: 'baseline',
      status: 'succeeded',
      inputHash: 'capital-official-hash-2',
      evidence: {
        status: 'current',
        rowsWithdrawn: 0,
        withdrawals: [],
      },
    },
  );
  trendOfficialGeneration += 1;
  const requestsBeforeHistoricalOfficial = officialSignalRequests.length;
  const beforeHistoricalOfficialCapital = counts.get('cap-dashboard') ?? 0;
  const beforeHistoricalOfficialLiquidity = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await queryClient!.invalidateQueries({
      predicate: (query) => query.queryKey[0] === 'official-run-signal',
    });
  });
  await waitFor(
    () => counts.get('cap-dashboard') === beforeHistoricalOfficialCapital + 1,
    'non-selected official run did not invalidate cached trend details',
  );
  assert.equal(
    counts.get('liq-dashboard'),
    beforeHistoricalOfficialLiquidity,
    'non-selected official run invalidated an unaffected module',
  );
  assert.equal(
    officialSignalRequests.length - requestsBeforeHistoricalOfficial,
    2,
    'official signal must remain bounded regardless of run-history size',
  );
  assert.deepEqual(
    officialSignalRequests.slice(-2),
    [
      { module: 'liquidity', scenarioCode: 'baseline', limit: 1, offset: 0 },
      { module: 'capital', scenarioCode: 'baseline', limit: 1, offset: 0 },
    ],
  );

  latestRegulatoryRuns.splice(
    0,
    latestRegulatoryRuns.length,
    ...latestRegulatoryRuns.filter(
      (run) => !run.id.startsWith('capital-failed-'),
    ),
  );

  const capitalOfficialIndex = latestRegulatoryRuns.findIndex(
    (run) => run.id === 'capital-official-2',
  );
  latestRegulatoryRuns[capitalOfficialIndex] = {
    ...latestRegulatoryRuns[capitalOfficialIndex],
    evidence: {
      status: 'inputs_withdrawn',
      rowsWithdrawn: 3,
      withdrawals: [
        {
          withdrawalId: 'withdrawal-1',
          approvedAt: '2026-08-27T12:30:00Z',
        },
      ],
    },
  };
  const beforeWithdrawalCapital = counts.get('cap-dashboard') ?? 0;
  const beforeWithdrawalLiquidity = counts.get('liq-dashboard') ?? 0;
  await act(async () => {
    await queryClient!.invalidateQueries({
      predicate: (query) => query.queryKey[0] === 'official-run-signal',
    });
  });
  await waitFor(
    () => counts.get('cap-dashboard') === beforeWithdrawalCapital + 1,
    'official-run evidence withdrawal did not invalidate capital detail',
  );
  assert.equal(counts.get('liq-dashboard'), beforeWithdrawalLiquidity);

  latestRegulatoryRuns[capitalOfficialIndex] = {
    ...latestRegulatoryRuns[capitalOfficialIndex],
    evidence: {
      status: 'current',
      rowsWithdrawn: 0,
      withdrawals: [],
    },
  };
  await act(async () => {
    await queryClient!.invalidateQueries({
      predicate: (query) => query.queryKey[0] === 'official-run-signal',
    });
  });
  await waitFor(
    () => counts.get('cap-dashboard') === beforeWithdrawalCapital + 2,
    'official-run evidence restoration did not invalidate capital detail',
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
      (counts.get(`cap-dashboard:${HISTORICAL_PERIOD_ID}`) ?? 0) >= 2,
    'historical dashboards did not reactivate before the official run',
  );
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
  globalThis.setInterval = nativeSetInterval;
  globalThis.setTimeout = nativeSetTimeout;
  console.log(
    'queryAuthorityBoundary.test.tsx: pending 0; settled 22 resources/35 calls; bounded official signal; idle and invalidation passed',
  );
}

void main().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
