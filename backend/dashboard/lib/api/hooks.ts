'use client';

/**
 * TanStack Query hooks over the generated risk-service client.
 *
 * Query keys: ['banks'], ['periods', bankId], ['liq-dashboard', bankId,
 * periodId], ['cap-dashboard', bankId, periodId], ['reg-runs', bankId, ...],
 * ['reg-run', bankId, runId], ['bsd3'|'bsd2', bankId, periodId],
 * ['cashflow-forecast', bankId, horizon, mode], ['cashflow-history', bankId,
 * days]. Mutations invalidate the related read keys.
 */

import {
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import type {
  BehavioralApplyProduct,
  CashflowForecastMode,
  CashflowHorizon,
  ForecastRunCreate,
  MarketDataConnectionCreate,
  MarketDataConnectionUpdate,
  RegulatoryModule,
  RegulatoryScenarioCode,
  WhatIfShockCode,
} from '@aequoros/risk-service-api';
import {
  ApiError,
  apiCall,
  banksApi,
  behavioralModelsApi,
  cashflowForecastApi,
  forecastingApi,
  isApiError,
  jobsApi,
  liveEngineApi,
  marketDataApi,
  regulatoryCapitalApi,
  regulatoryFtpApi,
  regulatoryFxApi,
  regulatoryIrrApi,
  regulatoryLiquidityApi,
  tenant,
} from './client';

const t = { xOrgId: tenant.orgId, xUserId: tenant.userId } as const;

/**
 * Polling cadence for the always-on "live" reads (live-summary, freshness,
 * alerts) and the module dashboards. The backend recomputes in the background
 * on ingestion, so the UI re-fetches on a timer to reflect it without a manual
 * refresh. Kept above the 30s query staleTime so a poll actually re-fetches.
 */
const LIVE_REFETCH_MS = 20_000;
const DASHBOARD_REFETCH_MS = 30_000;

export function useBanks() {
  return useQuery({
    queryKey: ['banks'],
    queryFn: () => apiCall(() => banksApi.listBanks({ ...t })),
  });
}

export function useBank(bankId: string | undefined) {
  return useQuery({
    queryKey: ['bank', bankId],
    queryFn: () => apiCall(() => banksApi.getBank({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useReportingPeriods(bankId: string | undefined) {
  return useQuery({
    queryKey: ['periods', bankId],
    queryFn: () =>
      apiCall(() => banksApi.listBankReportingPeriods({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useBankPeriodFacts(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['facts', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        banksApi.getBankPeriodFacts({
          ...t,
          bankId: bankId!,
          periodId: periodId!,
        })
      ),
    enabled: Boolean(bankId && periodId),
  });
}

export function useSeedDemoBank() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCall(() => banksApi.seedDemoBank({ ...t })),
    onSuccess: () => {
      void queryClient.invalidateQueries();
    },
  });
}

export function useLiquidityDashboard(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['liq-dashboard', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryLiquidityApi.getLiquidityDashboard({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useCapitalDashboard(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['cap-dashboard', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryCapitalApi.getCapitalDashboard({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useRegulatoryRuns(
  bankId: string | undefined,
  filters: {
    module?: RegulatoryModule;
    reportingPeriodId?: string;
    scenarioCode?: string;
    limit?: number;
    offset?: number;
  } = {}
) {
  return useQuery({
    queryKey: [
      'reg-runs',
      bankId,
      filters.module ?? null,
      filters.reportingPeriodId ?? null,
      filters.scenarioCode ?? null,
      filters.limit ?? 25,
      filters.offset ?? 0,
    ],
    queryFn: () =>
      apiCall(() =>
        regulatoryLiquidityApi.listRegulatoryRuns({
          ...t,
          bankId: bankId!,
          module: filters.module,
          reportingPeriodId: filters.reportingPeriodId,
          scenarioCode: filters.scenarioCode,
          limit: filters.limit,
          offset: filters.offset,
        })
      ),
    enabled: Boolean(bankId),
  });
}

export function useRegulatoryRun(
  bankId: string | undefined,
  runId: string | null | undefined
) {
  return useQuery({
    queryKey: ['reg-run', bankId, runId],
    queryFn: () =>
      apiCall(() =>
        regulatoryLiquidityApi.getRegulatoryRun({
          ...t,
          bankId: bankId!,
          runId: runId!,
        })
      ),
    enabled: Boolean(bankId && runId),
  });
}

const liquidityInvalidatePrefixes = [
  'liq-dashboard',
  'reg-runs',
  'reg-run',
  'bsd3',
];

const capitalInvalidatePrefixes = [
  'cap-dashboard',
  'cap-rwa',
  'cap-structure',
  'reg-runs',
  'reg-run',
  'bsd2',
];

export function useCreateRegulatoryRun(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      module: RegulatoryModule;
      reportingPeriodId: string;
      scenarioCode: RegulatoryScenarioCode;
    }) =>
      apiCall(() =>
        regulatoryLiquidityApi.createRegulatoryRun({
          ...t,
          bankId: bankId!,
          regulatoryRunCreate: payload,
        })
      ),
    onSuccess: (run) => {
      const prefixes =
        run.module === 'capital'
          ? capitalInvalidatePrefixes
          : liquidityInvalidatePrefixes;
      prefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useRunAllLiquidityScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryLiquidityApi.runAllLiquidityScenarios({
          ...t,
          bankId: bankId!,
          liquidityScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      liquidityInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useRunAllCapitalScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryCapitalApi.runAllCapitalScenarios({
          ...t,
          bankId: bankId!,
          capitalScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      capitalInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Interest Rate Risk (IRR), FX Risk, and Funds Transfer Pricing (FTP)
//
// Each module exposes a self-contained dashboard plus a run-all-scenarios
// batch mutation, mirroring the capital module. Run-all invalidates the
// module's dashboard and the shared regulatory-run read keys.
// ---------------------------------------------------------------------------

export function useIrrDashboard(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['irr-dashboard', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryIrrApi.getIrrDashboard({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useRunAllIrrScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryIrrApi.runAllIrrScenarios({
          ...t,
          bankId: bankId!,
          irrScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      ['irr-dashboard', 'reg-runs', 'reg-run'].forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useFxDashboard(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['fx-dashboard', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryFxApi.getFxDashboard({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useRunAllFxScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryFxApi.runAllFxScenarios({
          ...t,
          bankId: bankId!,
          fxScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      ['fx-dashboard', 'reg-runs', 'reg-run'].forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useFtpDashboard(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['ftp-dashboard', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryFtpApi.getFtpDashboard({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useRunAllFtpScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryFtpApi.runAllFtpScenarios({
          ...t,
          bankId: bankId!,
          ftpScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      ['ftp-dashboard', 'reg-runs', 'reg-run'].forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/** Whether an error is the BSD preview's "no baseline run yet" 409. */
export function isNoBaselineRunError(error: unknown): boolean {
  return isApiError(error) && error.errorCode === 'no_baseline_run';
}

export function useRwaBreakdown(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['cap-rwa', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryCapitalApi.getRwaBreakdown({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    retry: (failureCount, error) =>
      !isNoBaselineRunError(error) && failureCount < 1,
  });
}

export function useCapitalStructure(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['cap-structure', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryCapitalApi.getCapitalStructure({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId && periodId),
    retry: (failureCount, error) =>
      !isNoBaselineRunError(error) && failureCount < 1,
  });
}

export function useBsd3Preview(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['bsd3', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryLiquidityApi.getBsd3Preview({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId!,
        })
      ),
    enabled: Boolean(bankId && periodId),
    retry: (failureCount, error) =>
      !isNoBaselineRunError(error) && failureCount < 1,
  });
}

export function useBsd2Preview(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['bsd2', bankId, periodId],
    queryFn: () =>
      apiCall(() =>
        regulatoryCapitalApi.getBsd2Preview({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId!,
        })
      ),
    enabled: Boolean(bankId && periodId),
    retry: (failureCount, error) =>
      !isNoBaselineRunError(error) && failureCount < 1,
  });
}

const forecastInvalidatePrefixes = ['forecast-runs', 'forecast-run', 'reg-runs'];

export function useForecastScenarios(bankId: string | undefined) {
  return useQuery({
    queryKey: ['forecast-scenarios', bankId],
    queryFn: () =>
      apiCall(() =>
        forecastingApi.listForecastScenarios({ ...t, bankId: bankId! })
      ),
    enabled: Boolean(bankId),
  });
}

export function useForecastRuns(
  bankId: string | undefined,
  filters: { limit?: number; offset?: number } = {}
) {
  return useQuery({
    queryKey: [
      'forecast-runs',
      bankId,
      filters.limit ?? 25,
      filters.offset ?? 0,
    ],
    queryFn: () =>
      apiCall(() =>
        forecastingApi.listForecastRuns({
          ...t,
          bankId: bankId!,
          limit: filters.limit,
          offset: filters.offset,
        })
      ),
    enabled: Boolean(bankId),
  });
}

export function useForecastRun(
  bankId: string | undefined,
  runId: string | null | undefined
) {
  return useQuery({
    queryKey: ['forecast-run', bankId, runId],
    queryFn: () =>
      apiCall(() =>
        forecastingApi.getForecastRun({
          ...t,
          bankId: bankId!,
          runId: runId!,
        })
      ),
    enabled: Boolean(bankId && runId),
  });
}

export function useCreateForecastRun(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: ForecastRunCreate) =>
      apiCall(() =>
        forecastingApi.createForecastRun({
          ...t,
          bankId: bankId!,
          forecastRunCreate: payload,
        })
      ),
    onSuccess: () => {
      forecastInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useRunOptimizer(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        forecastingApi.runStrategicOptimizer({
          ...t,
          bankId: bankId!,
          optimizerRunCreate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reg-runs'] });
      void queryClient.invalidateQueries({ queryKey: ['reg-run'] });
    },
  });
}

export function useRunWhatIf(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      reportingPeriodId: string;
      shockCode: WhatIfShockCode;
    }) =>
      apiCall(() =>
        forecastingApi.runWhatIfAnalysis({
          ...t,
          bankId: bankId!,
          whatIfRunCreate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reg-runs'] });
      void queryClient.invalidateQueries({ queryKey: ['reg-run'] });
    },
  });
}

/** Whether an error is the cashflow proxy's "ML sidecar offline" 503. */
export function isServiceUnavailableError(error: unknown): boolean {
  return isApiError(error) && error.status === 503;
}

export function useCashflowForecast(
  bankId: string | undefined,
  horizon: CashflowHorizon,
  mode: CashflowForecastMode
) {
  return useQuery({
    queryKey: ['cashflow-forecast', bankId, horizon, mode],
    queryFn: () =>
      apiCall(() =>
        cashflowForecastApi.getCashflowForecast({
          ...t,
          bankId: bankId!,
          horizon,
          mode,
        })
      ),
    enabled: Boolean(bankId),
    retry: false,
    // First LSTM call trains the model — keep the result warm.
    staleTime: 5 * 60_000,
  });
}

export function useCashflowHistory(bankId: string | undefined, days: number) {
  return useQuery({
    queryKey: ['cashflow-history', bankId, days],
    queryFn: () =>
      apiCall(() =>
        cashflowForecastApi.getCashflowHistory({
          ...t,
          bankId: bankId!,
          days,
        })
      ),
    enabled: Boolean(bankId),
    retry: false,
    staleTime: 5 * 60_000,
  });
}

// ---------------------------------------------------------------------------
// Behavioral ML models — per-tenant NMD-duration / prepayment / deposit-stability
// ---------------------------------------------------------------------------

export type BehavioralModelSlug =
  | 'nmd-duration'
  | 'prepayment'
  | 'deposit-stability';

/** Read a model's per-product estimates (trains on the bank's history on first call). */
export function useBehavioralModel(
  bankId: string | undefined,
  model: BehavioralModelSlug
) {
  return useQuery({
    queryKey: ['behavioral-model', bankId, model],
    queryFn: () =>
      apiCall(() =>
        behavioralModelsApi.getBehavioralModel({ ...t, bankId: bankId!, model })
      ),
    enabled: Boolean(bankId),
    retry: false,
    // First call trains the model — keep the result warm.
    staleTime: 5 * 60_000,
  });
}

/** Retrain a model on the latest ingested history. */
export function useTrainBehavioralModel(
  bankId: string | undefined,
  model: BehavioralModelSlug
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiCall(() =>
        behavioralModelsApi.trainBehavioralModel({ ...t, bankId: bankId!, model })
      ),
    onSuccess: (result) => {
      queryClient.setQueryData(['behavioral-model', bankId, model], result);
    },
  });
}

/** Apply reviewed estimates as accepted behavioral assumptions the engines consume. */
export function useApplyBehavioralModel(
  bankId: string | undefined,
  model: BehavioralModelSlug
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (products: BehavioralApplyProduct[]) =>
      apiCall(() =>
        behavioralModelsApi.applyBehavioralModel({
          ...t,
          bankId: bankId!,
          model,
          behavioralApplyRequest: { products },
        })
      ),
    onSuccess: () => {
      // Downstream ALM facts change once assumptions are applied.
      ['behavioral-model', 'liquidity', 'ftp', 'irr', 'forecasting'].forEach(
        (prefix) => {
          void queryClient.invalidateQueries({ queryKey: [prefix] });
        }
      );
    },
  });
}

// ---------------------------------------------------------------------------
// Live engine — cross-module live view, per-module freshness, breach alerts,
// and the two background pipeline actions ("Recompute now" → /refresh,
// "Mint official run" → /official-runs).
//
// The three read hooks poll on a timer so the dashboard reflects the backend's
// automatic recompute-on-ingestion without any manual refresh. The two write
// hooks enqueue a job, poll it to completion, then invalidate every live read
// plus the module dashboards so the numbers and freshness badges update in one
// step.
// ---------------------------------------------------------------------------

/** As-of + reason payload for a pipeline action. */
export type PipelineActionInput = { asOfDate: string; reason: string };

/** Cross-module current metrics + per-module status, polled. */
export function useLiveSummary(bankId: string | undefined) {
  return useQuery({
    queryKey: ['live-summary', bankId],
    queryFn: () =>
      apiCall(() => liveEngineApi.getLiveSummary({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

/** Per-module live-vs-official-run freshness for a period, polled. */
export function useBankFreshness(
  bankId: string | undefined,
  periodId?: string | undefined
) {
  return useQuery({
    queryKey: ['freshness', bankId, periodId ?? null],
    queryFn: () =>
      apiCall(() =>
        liveEngineApi.getBankFreshness({
          ...t,
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

/** Open limit-breach alerts across modules, polled — powers the header bell. */
export function useBankAlerts(bankId: string | undefined, limit = 20) {
  return useQuery({
    queryKey: ['alerts', bankId, limit],
    queryFn: () =>
      apiCall(() =>
        liveEngineApi.getBankAlerts({ ...t, bankId: bankId!, limit })
      ),
    enabled: Boolean(bankId),
    refetchInterval: LIVE_REFETCH_MS,
  });
}

// Every read that a pipeline action can move. Invalidated (by prefix) once a
// refresh/official-run job completes so live numbers, freshness, alerts, and
// module dashboards all re-fetch together.
const livePipelineInvalidatePrefixes = [
  'live-summary',
  'freshness',
  'alerts',
  'liq-dashboard',
  'cap-dashboard',
  'irr-dashboard',
  'fx-dashboard',
  'ftp-dashboard',
  'cap-rwa',
  'cap-structure',
  'bsd3',
  'bsd2',
  'reg-runs',
  'reg-run',
  'forecast-runs',
  'facts',
  'periods',
];

/**
 * Poll a queued job to a terminal state. Resolves with the final job on
 * success; rejects with a normalized ApiError on failure. Bounded so a stuck
 * worker never hangs the mutation forever — on timeout it resolves with the
 * last-seen job so the UI can still refresh and show progress.
 */
async function pollJobToCompletion(
  jobId: string,
  { intervalMs = 1500, timeoutMs = 120_000 } = {}
) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    const job = await apiCall(() => jobsApi.getJob({ ...t, jobId }));
    if (job.status === 'succeeded') return job;
    if (job.status === 'failed') {
      throw new ApiError({
        message: job.error ?? 'The background job failed.',
        status: null,
        code: 'job_failed',
        errorCode: null,
        details: job.progress,
      });
    }
    if (Date.now() >= deadline) return job;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

/**
 * "Recompute now" — enqueue a live pipeline_refresh, poll it to completion, and
 * refresh every live read + module dashboard. Derives facts and recomputes live
 * metrics/findings without minting an immutable regulatory run.
 */
export function useRefreshBankData(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ asOfDate, reason }: PipelineActionInput) => {
      const enqueued = await apiCall(() =>
        liveEngineApi.refreshBankData({
          ...t,
          bankId: bankId!,
          refreshRequest: {
            asOfDate: new Date(`${asOfDate}T00:00:00Z`),
            reason,
          },
        })
      );
      return pollJobToCompletion(enqueued.jobId);
    },
    onSuccess: () => {
      livePipelineInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * "Mint official run for filing" — enqueue an immutable official run, poll it to
 * completion, and refresh every live read + module dashboard. The official run
 * is what clears the freshness "data changed since last official run" state.
 */
export function useMintOfficialRun(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({ asOfDate, reason }: PipelineActionInput) => {
      const enqueued = await apiCall(() =>
        liveEngineApi.mintOfficialRun({
          ...t,
          bankId: bankId!,
          officialRunRequest: {
            asOfDate: new Date(`${asOfDate}T00:00:00Z`),
            reason,
          },
        })
      );
      return pollJobToCompletion(enqueued.jobId);
    },
    onSuccess: () => {
      livePipelineInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Market data sources — vendor connection lifecycle (create / validate / test /
// rotate / disable / enable / revoke), the scope catalog with quota impact,
// per-vendor monthly quota, and the manual template upload.
//
// Query keys: ['md-connections', bankId], ['md-scopes', bankId],
// ['md-quota', bankId]. Every connection mutation invalidates the connections
// list; pull-affecting ones also invalidate the quota ledger.
// ---------------------------------------------------------------------------

const marketDataInvalidatePrefixes = ['md-connections', 'md-quota'];

export function useMarketDataConnections(bankId: string | undefined) {
  return useQuery({
    queryKey: ['md-connections', bankId],
    queryFn: () =>
      apiCall(() =>
        marketDataApi.listMarketDataConnections({ ...t, bankId: bankId! })
      ),
    enabled: Boolean(bankId),
  });
}

/** Every taxonomy scope with category, default frequency, vendor support, and
 * per-pull quota impact — drives the scope checkboxes in the add-source flow. */
export function useMarketDataScopes(bankId: string | undefined) {
  return useQuery({
    queryKey: ['md-scopes', bankId],
    queryFn: () =>
      apiCall(() => marketDataApi.listMarketDataScopes({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
    // The scope catalog is static per deployment — no need to refetch.
    staleTime: 10 * 60_000,
  });
}

export function useMarketDataQuota(bankId: string | undefined) {
  return useQuery({
    queryKey: ['md-quota', bankId],
    queryFn: () =>
      apiCall(() => marketDataApi.getMarketDataQuota({ ...t, bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useCreateMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: MarketDataConnectionCreate) =>
      apiCall(() =>
        marketDataApi.createMarketDataConnection({
          ...t,
          bankId: bankId!,
          marketDataConnectionCreate: payload,
        })
      ),
    onSuccess: () => {
      marketDataInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useValidateMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        marketDataApi.validateMarketDataConnection({
          ...t,
          bankId: bankId!,
          connectionId,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['md-connections'] });
    },
  });
}

/** Representative test pull (§9.2 step 5): returns human-readable sample
 * values on success, a bank-facing error otherwise. Never mutates state. */
export function useTestMarketDataConnection(bankId: string | undefined) {
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        marketDataApi.testMarketDataConnection({
          ...t,
          bankId: bankId!,
          connectionId,
        })
      ),
  });
}

/** Scope/schedule/name edits, and credential rotation when `credentials` is
 * present (validated vendor-side first; 422 with a bank-facing message on
 * failure, nothing changed). */
export function useUpdateMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      connectionId,
      payload,
    }: {
      connectionId: string;
      payload: MarketDataConnectionUpdate;
    }) =>
      apiCall(() =>
        marketDataApi.updateMarketDataConnection({
          ...t,
          bankId: bankId!,
          connectionId,
          marketDataConnectionUpdate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['md-connections'] });
    },
  });
}

export function useDisableMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        marketDataApi.disableMarketDataConnection({
          ...t,
          bankId: bankId!,
          connectionId,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['md-connections'] });
    },
  });
}

export function useEnableMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        marketDataApi.enableMarketDataConnection({
          ...t,
          bankId: bankId!,
          connectionId,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['md-connections'] });
    },
  });
}

/** Revoke (§10.5): wipes the stored credential, keeps the row for audit. */
export function useRevokeMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        marketDataApi.revokeMarketDataConnection({
          ...t,
          bankId: bankId!,
          connectionId,
        })
      ),
    onSuccess: () => {
      marketDataInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/** Run an uploaded template file as a manual market data pull (§8.3). */
export function useUploadMarketData(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, asOfDate }: { file: File; asOfDate: string }) =>
      apiCall(() =>
        marketDataApi.uploadMarketData({
          ...t,
          bankId: bankId!,
          file,
          asOfDate: new Date(`${asOfDate}T00:00:00Z`),
        })
      ),
    onSuccess: () => {
      marketDataInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
      // Manual pulls land canonical market data the same way ingestion does.
      void queryClient.invalidateQueries({ queryKey: ['de-batches', bankId] });
      void queryClient.invalidateQueries({ queryKey: ['de-summary', bankId] });
    },
  });
}
