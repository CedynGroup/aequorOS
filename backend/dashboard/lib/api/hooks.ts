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
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import type {
  ApprovalDecision,
  ArtifactKind,
  BehavioralApplyProduct,
  CashflowForecastMode,
  CashflowHorizon,
  ChannelCode,
  ForecastRunCreate,
  MarketDataConnectionCreate,
  MarketDataConnectionUpdate,
  PackageStatusFilter,
  RegulatoryArtifactRead,
  RegulatoryModule,
  RegulatoryScenarioCode,
  TemenosBackfillRequest,
  TemenosConnectionCreate,
  TemenosConnectionUpdate,
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
  regulatoryReportingApi,
  temenosApi,
} from './client';
import { ingestionApi } from './ingestion';


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
    queryFn: () => apiCall(() => banksApi.listBanks({})),
  });
}

export function useBank(bankId: string | undefined) {
  return useQuery({
    queryKey: ['bank', bankId],
    queryFn: () => apiCall(() => banksApi.getBank({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useReportingPeriods(bankId: string | undefined) {
  return useQuery({
    queryKey: ['periods', bankId],
    queryFn: () =>
      apiCall(() => banksApi.listBankReportingPeriods({ bankId: bankId! })),
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
    mutationFn: () => apiCall(() => banksApi.seedDemoBank({})),
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
        forecastingApi.listForecastScenarios({ bankId: bankId! })
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
        behavioralModelsApi.getBehavioralModel({ bankId: bankId!, model })
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
        behavioralModelsApi.trainBehavioralModel({ bankId: bankId!, model })
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
      apiCall(() => liveEngineApi.getLiveSummary({ bankId: bankId! })),
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
        liveEngineApi.getBankAlerts({ bankId: bankId!, limit })
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
    const job = await apiCall(() => jobsApi.getJob({ jobId }));
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
        marketDataApi.listMarketDataConnections({ bankId: bankId! })
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
      apiCall(() => marketDataApi.listMarketDataScopes({ bankId: bankId! })),
    enabled: Boolean(bankId),
    // The scope catalog is static per deployment — no need to refetch.
    staleTime: 10 * 60_000,
  });
}

export function useMarketDataQuota(bankId: string | undefined) {
  return useQuery({
    queryKey: ['md-quota', bankId],
    queryFn: () =>
      apiCall(() => marketDataApi.getMarketDataQuota({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useCreateMarketDataConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: MarketDataConnectionCreate) =>
      apiCall(() =>
        marketDataApi.createMarketDataConnection({
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

/**
 * Vendor-blind market data consumption views for the Markets hub: every
 * yield curve, FX spot (+ trailing history for sparklines), issuer rating,
 * and macro index the canonical store can serve at the as-of date, each with
 * source attribution and freshness. Omit `asOf` for "today" (latest pulls).
 */
export function useMarketDataViews(bankId: string | undefined, asOf?: string) {
  return useQuery({
    queryKey: ['md-views', bankId, asOf ?? null],
    queryFn: () =>
      apiCall(() =>
        marketDataApi.getMarketDataViews({
          bankId: bankId!,
          asOf: asOf ? new Date(`${asOf}T00:00:00Z`) : undefined,
        })
      ),
    enabled: Boolean(bankId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

/** Server-side filters + window for one page of the canonical position book. */
export type CanonicalPositionsPageParams = {
  limit: number;
  offset: number;
  positionType?: string;
  currency?: string;
  q?: string;
};

/**
 * One server-paginated page of the /positions blotter. The endpoint filters
 * and counts server-side (`total` spans the filtered set), so this scales to
 * six-figure books. `keepPreviousData` keeps the current page on screen
 * while the next one loads — page turns swap data without a layout collapse.
 */
export function useCanonicalPositionsPage(
  bankId: string | undefined,
  { limit, offset, positionType, currency, q }: CanonicalPositionsPageParams
) {
  return useQuery({
    queryKey: [
      'positions-page',
      bankId,
      limit,
      offset,
      positionType ?? null,
      currency ?? null,
      q ?? null,
    ],
    queryFn: () =>
      apiCall(() =>
        ingestionApi.listCanonicalPositions({
          bankId: bankId!,
          limit,
          offset,
          positionType: positionType || undefined,
          currency: currency || undefined,
          q: q || undefined,
        })
      ),
    enabled: Boolean(bankId),
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

/**
 * Distinct position types and currencies with current-generation counts —
 * feeds the blotter's filter dropdowns and KPIs without paging the book.
 */
export function useCanonicalPositionFacets(bankId: string | undefined) {
  return useQuery({
    queryKey: ['positions-facets', bankId],
    queryFn: () =>
      apiCall(() =>
        ingestionApi.listCanonicalPositionFacets({ bankId: bankId! })
      ),
    enabled: Boolean(bankId),
    staleTime: 5 * 60_000,
  });
}

/** Run an uploaded template file as a manual market data pull (§8.3). */
export function useUploadMarketData(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ file, asOfDate }: { file: File; asOfDate: string }) =>
      apiCall(() =>
        marketDataApi.uploadMarketData({
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

// ---------------------------------------------------------------------------
// Temenos T24 core-banking connections (docs/temenos_adapter.md)
// ---------------------------------------------------------------------------

const temenosInvalidatePrefixes = ['t24-connections'];

export function useTemenosConnections(bankId: string | undefined) {
  return useQuery({
    queryKey: ['t24-connections', bankId],
    queryFn: () =>
      apiCall(() => temenosApi.listTemenosConnections({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

/** The core-banking domain catalog for a connection mode: category, canonical
 * entity type, default cadence, and whether the mode catalog supports it. */
export function useTemenosDomains(bankId: string | undefined, mode: string) {
  return useQuery({
    queryKey: ['t24-domains', bankId, mode],
    queryFn: () =>
      apiCall(() => temenosApi.listTemenosDomains({ bankId: bankId!, mode })),
    enabled: Boolean(bankId),
    staleTime: 10 * 60_000,
  });
}

export function useCreateTemenosConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: TemenosConnectionCreate) =>
      apiCall(() =>
        temenosApi.createTemenosConnection({
          bankId: bankId!,
          temenosConnectionCreate: payload,
        })
      ),
    onSuccess: () => {
      temenosInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useValidateTemenosConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        temenosApi.validateTemenosConnection({ bankId: bankId!, connectionId })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['t24-connections'] });
    },
  });
}

/** Signs on and reports the pull plan; a live pull runs once the transport is
 * enabled. Never mutates state. */
export function useTestTemenosConnection(bankId: string | undefined) {
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        temenosApi.testTemenosConnection({ bankId: bankId!, connectionId })
      ),
  });
}

/** Config edits and credential rotation (validated first; 422 with a
 * bank-facing message on failure, nothing changed). */
export function useUpdateTemenosConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      connectionId,
      payload,
    }: {
      connectionId: string;
      payload: TemenosConnectionUpdate;
    }) =>
      apiCall(() =>
        temenosApi.updateTemenosConnection({
          bankId: bankId!,
          connectionId,
          temenosConnectionUpdate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['t24-connections'] });
    },
  });
}

export function useDisableTemenosConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        temenosApi.disableTemenosConnection({ bankId: bankId!, connectionId })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['t24-connections'] });
    },
  });
}

export function useEnableTemenosConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        temenosApi.enableTemenosConnection({ bankId: bankId!, connectionId })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['t24-connections'] });
    },
  });
}

export function useRevokeTemenosConnection(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (connectionId: string) =>
      apiCall(() =>
        temenosApi.revokeTemenosConnection({ bankId: bankId!, connectionId })
      ),
    onSuccess: () => {
      temenosInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/** On-demand pull for one as-of date (defaults to today). Enqueues a coalesced
 * temenos_pull job. */
export function useTriggerTemenosPull(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      connectionId,
      asOfDate,
    }: {
      connectionId: string;
      asOfDate?: string;
    }) =>
      apiCall(() =>
        temenosApi.triggerTemenosPull({
          bankId: bankId!,
          connectionId,
          // The generated client types this nullable date as an ISO string.
          temenosPullTriggerRequest: {
            asOfDate: asOfDate ? `${asOfDate}T00:00:00Z` : undefined,
          },
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['t24-connections'] });
    },
  });
}

/** Historical backfill: one pull job per as-of date across an inclusive range. */
export function useTriggerTemenosBackfill(bankId: string | undefined) {
  return useMutation({
    mutationFn: ({
      connectionId,
      payload,
    }: {
      connectionId: string;
      payload: TemenosBackfillRequest;
    }) =>
      apiCall(() =>
        temenosApi.triggerTemenosBackfill({
          bankId: bankId!,
          connectionId,
          temenosBackfillRequest: payload,
        })
      ),
  });
}

// ---------------------------------------------------------------------------
// Regulatory Reporting & Submission Hub (docs/regulatory_reporting.md)
//
// Query keys: ['rr-obligations', bankId, horizon], ['rr-packages', bankId,
// ...filters], ['rr-package', bankId, packageId], ['rr-events', bankId,
// packageId], ['rr-templates'], ['rr-channel-config', bankId, channel],
// ['rr-artifacts', bankId, packageId] (session-local export ledger — the API
// exposes no artifact-list endpoint, so exports minted this session are the
// downloadable set). Package mutations invalidate obligations + package reads.
// ---------------------------------------------------------------------------

const reportingInvalidatePrefixes = [
  'rr-obligations',
  'rr-packages',
  'rr-package',
  'rr-events',
];

/** Deadline board: every registry obligation in the horizon with RAG + package. */
export function useReportingObligations(
  bankId: string | undefined,
  horizonMonths = 3
) {
  return useQuery({
    queryKey: ['rr-obligations', bankId, horizonMonths],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listReportingObligations({
          bankId: bankId!,
          horizonMonths,
        })
      ),
    enabled: Boolean(bankId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export type RegulatoryPackageFilters = {
  returnCode?: string;
  /** ISO date (YYYY-MM-DD). */
  reportingDate?: string;
  status?: PackageStatusFilter;
  includeSuperseded?: boolean;
  limit?: number;
  offset?: number;
};

export function useRegulatoryPackages(
  bankId: string | undefined,
  filters: RegulatoryPackageFilters = {}
) {
  return useQuery({
    queryKey: [
      'rr-packages',
      bankId,
      filters.returnCode ?? null,
      filters.reportingDate ?? null,
      filters.status ?? null,
      filters.includeSuperseded ?? true,
      filters.limit ?? 25,
      filters.offset ?? 0,
    ],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listRegulatoryPackages({
          bankId: bankId!,
          returnCode: filters.returnCode,
          reportingDate: filters.reportingDate
            ? new Date(`${filters.reportingDate}T00:00:00Z`)
            : undefined,
          status: filters.status,
          includeSuperseded: filters.includeSuperseded,
          limit: filters.limit,
          offset: filters.offset,
        })
      ),
    enabled: Boolean(bankId),
  });
}

export function useRegulatoryPackage(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['rr-package', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.getRegulatoryPackage({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/** Generate (or regenerate — new version, prior becomes superseded). */
export function useGenerateRegulatoryPackage(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      returnCode: string;
      /** ISO date (YYYY-MM-DD). */
      reportingDate: string;
      notes?: string;
    }) =>
      apiCall(() =>
        regulatoryReportingApi.createRegulatoryPackage({
          bankId: bankId!,
          regulatoryPackageCreate: {
            returnCode: payload.returnCode,
            reportingDate: new Date(`${payload.reportingDate}T00:00:00Z`),
            notes: payload.notes ?? null,
          },
        })
      ),
    onSuccess: () => {
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useValidateRegulatoryPackage(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (packageId: string) =>
      apiCall(() =>
        regulatoryReportingApi.validateRegulatoryPackage({
          bankId: bankId!,
          packageId,
        })
      ),
    onSuccess: (pkg) => {
      queryClient.setQueryData(['rr-package', bankId, pkg.id], pkg);
      void queryClient.invalidateQueries({ queryKey: ['rr-packages'] });
      void queryClient.invalidateQueries({ queryKey: ['rr-obligations'] });
    },
  });
}

export function useRequestPackageApproval(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ packageId, reason }: { packageId: string; reason?: string }) =>
      apiCall(() =>
        regulatoryReportingApi.requestPackageApproval({
          bankId: bankId!,
          packageId,
          packageApprovalRequestCreate: { reason: reason ?? null },
        })
      ),
    onSuccess: (pkg) => {
      queryClient.setQueryData(['rr-package', bankId, pkg.id], pkg);
      void queryClient.invalidateQueries({ queryKey: ['rr-packages'] });
      void queryClient.invalidateQueries({ queryKey: ['rr-obligations'] });
    },
  });
}

/**
 * Maker-checker decision. `actingUserId` overrides the X-User-Id header for
 * the demo "acting as a second officer" affordance — production derives the
 * checker from the login. Deciding as the generator returns the backend's
 * maker-checker 409, surfaced verbatim.
 */
export function useDecidePackageApproval(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      action,
      reason,
      actingUserId,
    }: {
      packageId: string;
      action: ApprovalDecision;
      reason?: string;
      actingUserId?: string;
    }) =>
      // The decision is attributed to the authenticated checker (from the verified
      // token) — you cannot approve "as" another user. actingUserId is accepted for
      // backward compatibility but no longer overrides the identity.
      apiCall(() =>
        regulatoryReportingApi.decidePackageApproval({
          bankId: bankId!,
          packageId,
          packageApprovalDecisionCreate: { action, reason: reason ?? null },
        })
      ),
    onSuccess: (pkg) => {
      queryClient.setQueryData(['rr-package', bankId, pkg.id], pkg);
      void queryClient.invalidateQueries({ queryKey: ['rr-packages'] });
      void queryClient.invalidateQueries({ queryKey: ['rr-obligations'] });
    },
  });
}

/**
 * Session-local artifact ledger for one package. The API has no artifact-list
 * endpoint (exports are minted on demand), so artifacts exported in this
 * session accumulate here via useExportRegulatoryPackage. Never invalidated.
 */
export function useSessionArtifacts(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery<RegulatoryArtifactRead[]>({
    queryKey: ['rr-artifacts', bankId, packageId],
    queryFn: () => [],
    enabled: Boolean(bankId && packageId),
    staleTime: Infinity,
    gcTime: Infinity,
  });
}

/** Mint one export artifact (xlsx/csv/pdf) and record it in the session ledger. */
export function useExportRegulatoryPackage(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ packageId, kind }: { packageId: string; kind: ArtifactKind }) =>
      apiCall(() =>
        regulatoryReportingApi.exportRegulatoryPackage({
          bankId: bankId!,
          packageId,
          kind,
        })
      ),
    onSuccess: (artifact) => {
      queryClient.setQueryData<RegulatoryArtifactRead[]>(
        ['rr-artifacts', bankId, artifact.packageId],
        (prev = []) => [
          ...prev.filter((entry) => entry.id !== artifact.id),
          artifact,
        ]
      );
    },
  });
}

/**
 * Submit via the requested channel (omit for the registry default). ORASS
 * downtime surfaces as a structured 409 with errorCode 'channel_downtime'
 * and a `fallback` block in details — the UI renders the email-fallback panel.
 */
export function useSubmitRegulatoryPackage(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      channel,
    }: {
      packageId: string;
      channel?: ChannelCode;
    }) =>
      apiCall(() =>
        regulatoryReportingApi.submitRegulatoryPackage({
          bankId: bankId!,
          packageId,
          packageSubmitCreate: { channel: channel ?? null },
        })
      ),
    onSuccess: (pkg) => {
      queryClient.setQueryData(['rr-package', bankId, pkg.id], pkg);
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/** One poll cycle against the latest channel submission; records decisions. */
export function usePollRegulatorySubmission(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (packageId: string) =>
      apiCall(() =>
        regulatoryReportingApi.pollRegulatorySubmission({
          bankId: bankId!,
          packageId,
        })
      ),
    onSuccess: (poll) => {
      queryClient.setQueryData(['rr-package', bankId, poll._package.id], poll._package);
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useSubmissionEvents(
  bankId: string | undefined,
  packageId: string | null | undefined,
  limit = 50
) {
  return useQuery({
    queryKey: ['rr-events', bankId, packageId, limit],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listSubmissionEvents({
          bankId: bankId!,
          packageId: packageId!,
          limit,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/** Preview the BG/FMD/2026/07 downtime email bundle without submitting. */
export function useEmailFallbackInstructions(
  bankId: string | undefined,
  packageId: string | null | undefined,
  enabled = true
) {
  return useQuery({
    queryKey: ['rr-email-fallback', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.getEmailFallbackInstructions({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId) && enabled,
  });
}

/** The return-template registry (citations, fidelity grades, default channels). */
export function useReturnTemplates() {
  return useQuery({
    queryKey: ['rr-templates'],
    queryFn: () => apiCall(() => regulatoryReportingApi.listReturnTemplates({})),
    staleTime: 10 * 60_000,
  });
}

/** Whether an error is the "no channel config yet" 404 (unconfigured state). */
export function isChannelConfigMissingError(error: unknown): boolean {
  return isApiError(error) && error.status === 404;
}

export function useChannelConfig(
  bankId: string | undefined,
  channel: ChannelCode
) {
  return useQuery({
    queryKey: ['rr-channel-config', bankId, channel],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.getChannelConfig({
          bankId: bankId!,
          channel,
        })
      ),
    enabled: Boolean(bankId),
    retry: false,
  });
}

/** Upsert one channel config; `credentials` is write-only (fingerprint back). */
export function useSaveChannelConfig(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      channel,
      config,
      credentials,
    }: {
      channel: ChannelCode;
      config: Record<string, unknown>;
      credentials?: Record<string, unknown>;
    }) =>
      apiCall(() =>
        regulatoryReportingApi.putChannelConfig({
          bankId: bankId!,
          channel,
          channelConfigPut: {
            config,
            credentials: credentials ?? null,
          },
        })
      ),
    onSuccess: (config) => {
      queryClient.setQueryData(
        ['rr-channel-config', bankId, config.channel],
        config
      );
    },
  });
}
