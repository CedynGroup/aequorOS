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
  moduleWithLoader._load = (request, parent, isMain) => {
    if (request === '@aequoros/risk-service-api') return generatedApi;
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
  const { default: QueryAuthorityBoundary } = await import('./QueryAuthorityBoundary');
  const { queryAuthorityScope } = await import('./queryPolicy');
  const hooks = await import('./hooks');
  const ingestion = await import('./ingestion');
  const clients = await import('./client');
  moduleWithLoader._load = originalLoad;

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

  const scope = queryAuthorityScope(
    'OR-DEM00001',
    'analyst@aequoros.example',
    ['analyst'],
  );
  let renderer: ReturnType<typeof create>;
  await act(async () => {
    renderer = create(
      <QueryAuthorityBoundary scope={null} fallback={<span>loading</span>}>
        <CommandCenterHookHarness />
      </QueryAuthorityBoundary>,
    );
    await new Promise((resolve) => setTimeout(resolve, 70));
  });
  assert.equal(counts.size, 0, 'unresolved authority must not mount query consumers');

  await act(async () => {
    renderer.update(
      <QueryAuthorityBoundary scope={scope} fallback={<span>loading</span>}>
        <CommandCenterHookHarness />
      </QueryAuthorityBoundary>,
    );
  });
  await waitFor(
    () => [...counts.entries()].filter(([name]) => name !== 'liq-mutation').length === 20,
    'Command Center resources did not settle',
  );
  const initialCounts = new Map(counts);
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
