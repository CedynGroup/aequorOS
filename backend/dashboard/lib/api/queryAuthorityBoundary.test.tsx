import assert from 'node:assert/strict';

const BANK_ID = 'BK-SAMP0001';
const PERIOD_ID = 'period-latest';

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
  globalThis.setInterval = ((handler: TimerHandler, timeout?: number, ...args: unknown[]) =>
    nativeSetInterval(handler, timeout && timeout >= 1_000 ? 30 : timeout, ...args)) as typeof setInterval;

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

  let releaseStatus: (() => void) | null = null;
  const statusGate = new Promise<void>((resolve) => {
    releaseStatus = resolve;
  });
  globalThis.fetch = (async () => {
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
  const liveSummary = async () => {
    counts.set('live-summary', (counts.get('live-summary') ?? 0) + 1);
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
    getLiquidityDashboard: response('liq-dashboard', {}),
    runAllLiquidityScenarios: response('liq-mutation', {}),
  });
  mock(clients.regulatoryCapitalApi, {
    getCapitalDashboard: response('cap-dashboard', {}),
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
    getBankAlerts: response('alerts', {}),
    listLiveSnapshots: async ({ module: liveModule }: { module: string }) => {
      const name = `live-snapshots:${liveModule}`;
      counts.set(name, (counts.get(name) ?? 0) + 1);
      return {};
    },
  });
  mock(clients.notificationsApi, {
    listNotifications: response('notifications', {}),
  });
  mock(ingestion.ingestionApi, {
    listIngestionBatches: response('de-batches', {}),
    listBankDataActivations: response('de-activations', {}),
  });

  let queryClient: ReturnType<typeof useQueryClient> | null = null;
  let runLiquidityScenarios: (() => Promise<unknown>) | null = null;
  let authorityMounts = 0;
  let resolvedAuthority: ReturnType<typeof useQueryAuthorityScope> | null = null;

  function AuthorityProbe() {
    const authority = useQueryAuthorityScope();
    React.useEffect(() => {
      authorityMounts += 1;
      resolvedAuthority = authority;
    }, [authority]);
    return null;
  }

  function CommandCenterHookHarness() {
    queryClient = useQueryClient();
    hooks.useBanks();
    hooks.useReportingPeriods(BANK_ID);
    hooks.useBankPeriodFacts(BANK_ID, PERIOD_ID);
    hooks.useBankPeriodFacts(BANK_ID, PERIOD_ID);
    hooks.useLiveSummary(BANK_ID);
    hooks.useLiveSummary(BANK_ID);
    hooks.useBankAlerts(BANK_ID);
    hooks.useBankAlerts(BANK_ID);
    hooks.useNotifications();
    hooks.useNotifications();
    hooks.useLiquidityDashboard(BANK_ID);
    hooks.useLiquidityDashboard(BANK_ID);
    hooks.useCapitalDashboard(BANK_ID);
    hooks.useCapitalDashboard(BANK_ID);
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

  await act(async () => {
    releaseStatus!();
  });
  await waitFor(
    () => [...counts.entries()].filter(([name]) => name !== 'liq-mutation').length === 20,
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
    20,
    'real duplicate consumers must collapse to one request per resource',
  );

  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 70));
  });
  assert.equal(counts.get('liq-dashboard'), 1, 'heavy detail must remain idle');
  assert.equal(counts.get('cap-dashboard'), 1, 'heavy detail must remain idle');

  await act(async () => {
    await runLiquidityScenarios!();
  });
  await waitFor(() => counts.get('liq-dashboard') === 2, 'mutation did not invalidate detail');

  generation += 1;
  await act(async () => {
    await queryClient!.invalidateQueries({ queryKey: ['live-summary'] });
  });
  await waitFor(
    () => counts.get('liq-dashboard') === 3,
    'live generation change did not invalidate detail',
  );

  await act(async () => renderer!.unmount());
  globalThis.setInterval = nativeSetInterval;
  console.log(
    'queryAuthorityBoundary.test.tsx: pending 0; settled 20 resources/20 calls; equivalent detail deduped; idle and invalidation passed',
  );
}

void main().then(
  () => process.exit(0),
  (error) => {
    console.error(error);
    process.exit(1);
  },
);
