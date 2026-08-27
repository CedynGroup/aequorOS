'use client';

/**
 * TanStack Query hooks over the generated risk-service client.
 *
 * Home and detailed module keys use the authority-scoped factories in
 * queryPolicy.ts: prefix → tenant → authority → bank → semantic dimensions.
 * Older non-home hooks retain their prefix-first shapes during migration; the
 * QueryClient itself remounts at every authority boundary. Mutations invalidate
 * the related bank-local reads.
 */

import { useCallback, useEffect, useRef } from 'react';
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query';
import type { QueryClient } from '@tanstack/react-query';
import type {
  LiveModule,
  AnalysisRunCreate,
  SavedAnalysisCreate,
  StressScenarioCreate,
  StressScenarioUpdate,
  WorkbenchModule,
  AdoptSignatureRequest,
  ApprovalDecision,
  ArtifactKind,
  AttestationStatusRead,
  BankLicenseCreate,
  BankLicenseUpdate,
  BankNameHistoryCreate,
  BankNameHistoryUpdate,
  BankProductCreate,
  BankProductUpdate,
  BehavioralApplyProduct,
  CashflowForecastMode,
  CashflowForecastScenario,
  CashflowHorizon,
  CertifyAndSendRequest,
  ChannelCode,
  CrmHaircutUpdate,
  EclAssumptionUpdate,
  EwiRegisterPut,
  ForecastRunCreate,
  InstitutionProfilePut,
  LiquidityThresholdUpdate,
  MarketDataConnectionCreate,
  MarketDataConnectionUpdate,
  MarketDataOverlayCreate,
  OutletCreate,
  OutletUpdate,
  PackageStatusFilter,
  PolicyUpsertRequest,
  RegulatoryModule,
  RegulatoryScenarioCode,
  RelatedPartyCreate,
  RelatedPartyUpdate,
  ShareholdingCreate,
  ShareholdingUpdate,
  SignatureFieldPlacement,
  SignaturePlacementTemplateUpsertRequest,
  SigningRole,
  TemenosBackfillRequest,
  TemenosConnectionCreate,
  TemenosConnectionUpdate,
  WhatIfShockCode,
} from '@aequoros/risk-service-api';
import {
  ApiError,
  apiCall,
  attestationApi,
  banksApi,
  behavioralModelsApi,
  cashflowForecastApi,
  cashflowWindowApi,
  creditParamsApi,
  forecastingApi,
  institutionProfileApi,
  integrationKeysApi,
  liquidityCfpApi,
  liquidityThresholdsApi,
  isApiError,
  jobsApi,
  reverseStressApi,
  scenarioWorkbenchApi,
  liveEngineApi,
  windowAnalyticsApi,
  marketDataApi,
  ModuleUnavailableError,
  notificationsApi,
  organizationApi,
  regulatoryCapitalApi,
  regulatoryFtpApi,
  regulatoryFxApi,
  regulatoryIrrApi,
  regulatoryLiquidityApi,
  regulatoryReportingApi,
  temenosApi,
} from './client';
import { ingestionApi } from './ingestion';
import {
  getForwardGrid,
  getMarketDataPlanes,
  getMarketDataSourcePreferences,
  putMarketDataSourcePreferences,
  type MarketDataCategory,
  type MarketDataSourcePreferencesPatch,
} from './marketDataSources';
import {
  getReportComparison,
  type ReportComparisonParams,
} from './reportComparison';
import {
  HEAVY_DASHBOARD_QUERY_POLICY,
  LIVE_SIGNAL_POLL_MS,
  changedGenerations,
  dashboardQueryKey,
  dashboardSemantic,
  generationFingerprint,
  invalidateGenerationChanges,
  invalidateOfficialRunChanges,
  invalidateScopedPrefixes,
  jitteredPollInterval,
  officialRunFingerprint,
  regulatoryDetailInvalidationPrefixes,
  scopedQueryKey,
  waitForInitialDashboardSignals,
  type QueryAuthorityScope,
} from './queryPolicy';
import { useQueryAuthorityScope } from './useQueryScope';


const DASHBOARD_REFETCH_MS = 30_000;

export function useBanks() {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey('banks', scope),
    queryFn: () => apiCall(() => banksApi.listBanks({})),
  });
}

export function useBank(bankId: string | undefined) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey('bank', scope, bankId ?? null),
    queryFn: () => apiCall(() => banksApi.getBank({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useReportingPeriods(bankId: string | undefined) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey('periods', scope, bankId ?? null),
    queryFn: () =>
      apiCall(() => banksApi.listBankReportingPeriods({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useBankPeriodFacts(
  bankId: string | undefined,
  periodId: string | undefined
) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey('facts', scope, bankId ?? null, periodId ?? null),
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

export function useLiquidityDashboard(
  bankId: string | undefined,
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  const semantic = dashboardSemantic(periodId);
  return useQuery({
    queryKey: dashboardQueryKey('liq-dashboard', scope, bankId, semantic),
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        regulatoryLiquidityApi.getLiquidityDashboard({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      );
    },
    enabled: Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useCapitalDashboard(
  bankId: string | undefined,
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  const semantic = dashboardSemantic(periodId);
  return useQuery({
    queryKey: dashboardQueryKey('cap-dashboard', scope, bankId, semantic),
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        regulatoryCapitalApi.getCapitalDashboard({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      );
    },
    enabled: Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

export function useEffectiveRatioDashboards(
  bankId: string | undefined,
  periodId: string,
) {
  const currentLiq = useLiquidityDashboard(bankId);
  const currentCap = useCapitalDashboard(bankId);
  const establishedLiqPeriod = useRef<string | null>(null);
  const establishedCapPeriod = useRef<string | null>(null);
  if (!currentLiq.isFetching) {
    establishedLiqPeriod.current =
      currentLiq.isError ||
      Boolean(currentLiq.data && currentLiq.data.period.id !== periodId)
        ? periodId
        : null;
  }
  if (!currentCap.isFetching) {
    establishedCapPeriod.current =
      currentCap.isError ||
      Boolean(currentCap.data && currentCap.data.period.id !== periodId)
        ? periodId
        : null;
  }
  const needsPeriodLiq = establishedLiqPeriod.current === periodId;
  const needsPeriodCap = establishedCapPeriod.current === periodId;
  const periodLiq = useLiquidityDashboard(
    needsPeriodLiq ? bankId : undefined,
    periodId,
  );
  const periodCap = useCapitalDashboard(
    needsPeriodCap ? bankId : undefined,
    periodId,
  );
  return {
    liquidity: needsPeriodLiq ? periodLiq : currentLiq,
    capital: needsPeriodCap ? periodCap : currentCap,
  };
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
  const scope = useQueryAuthorityScope();
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
      void invalidateScopedPrefixes(queryClient, prefixes, scope, bankId);
    },
  });
}

export function useRunAllLiquidityScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryLiquidityApi.runAllLiquidityScenarios({
          bankId: bankId!,
          liquidityScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      void invalidateScopedPrefixes(
        queryClient,
        liquidityInvalidatePrefixes,
        scope,
        bankId,
      );
    },
  });
}

export function useRunAllCapitalScenarios(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (payload: { reportingPeriodId: string }) =>
      apiCall(() =>
        regulatoryCapitalApi.runAllCapitalScenarios({
          bankId: bankId!,
          capitalScenarioBatchCreate: payload,
        })
      ),
    onSuccess: () => {
      void invalidateScopedPrefixes(
        queryClient,
        capitalInvalidatePrefixes,
        scope,
        bankId,
      );
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
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: dashboardQueryKey(
      'irr-dashboard',
      scope,
      bankId,
      dashboardSemantic(periodId),
    ),
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(async () => {
        // The live IRR service returns HTTP 200 with an availability envelope
        // while current facts lack a compatible analysis context. Detect it
        // before generated-client deserialization expects dashboard arrays.
        const response = await regulatoryIrrApi.getIrrDashboardRaw({
          bankId: bankId!,
          reportingPeriodId: periodId,
        });
        const payload = (await response.raw.clone().json()) as {
          available?: boolean;
          error_code?: string;
          reason?: string;
        };
        if (payload.available === false && payload.error_code) {
          throw new ModuleUnavailableError(
            payload.reason ?? 'Interest-rate risk analysis is not available yet.',
            payload.error_code
          );
        }
        return response.value();
      });
    },
    enabled: Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
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
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: dashboardQueryKey(
      'fx-dashboard',
      scope,
      bankId,
      dashboardSemantic(periodId),
    ),
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        regulatoryFxApi.getFxDashboard({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      );
    },
    enabled: Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
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
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: dashboardQueryKey(
      'ftp-dashboard',
      scope,
      bankId,
      dashboardSemantic(periodId),
    ),
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        regulatoryFtpApi.getFtpDashboard({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      );
    },
    enabled: Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
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
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ['cap-rwa', bankId, periodId],
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        regulatoryCapitalApi.getRwaBreakdown({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      );
    },
    enabled: Boolean(bankId),
    retry: (failureCount, error) =>
      !isNoBaselineRunError(error) && failureCount < 1,
  });
}

export function useCapitalStructure(
  bankId: string | undefined,
  periodId?: string | undefined
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: ['cap-structure', bankId, periodId],
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        regulatoryCapitalApi.getCapitalStructure({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      );
    },
    enabled: Boolean(bankId),
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
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: [
      'forecast-runs',
      bankId,
      filters.limit ?? 25,
      filters.offset ?? 0,
    ],
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        forecastingApi.listForecastRuns({
          bankId: bankId!,
          limit: filters.limit,
          offset: filters.offset,
        })
      );
    },
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
  mode: CashflowForecastMode,
  scenario: CashflowForecastScenario
) {
  return useQuery({
    queryKey: ['cashflow-forecast', bankId, horizon, mode, scenario],
    queryFn: () =>
      apiCall(() =>
        cashflowForecastApi.getCashflowForecast({
          bankId: bankId!,
          horizon,
          mode,
          scenario,
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

/** Observed deposit behavior by product, segment, connected group, and branch. */
export function useBehavioralLiquidity(bankId: string | undefined) {
  return useQuery({
    queryKey: ['behavioral-liquidity', bankId],
    queryFn: () =>
      apiCall(() => behavioralModelsApi.getBehavioralLiquidity({ bankId: bankId! })),
    enabled: Boolean(bankId),
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
// The cheap read hooks poll (with stable tenant jitter) so the dashboard
// reflects background work. A live-summary generation change invalidates the
// affected detailed module payloads; those heavyweight reads never poll on
// their own. The two write hooks also invalidate once their jobs complete.
// ---------------------------------------------------------------------------

/** As-of + reason payload for a pipeline action. */
export type PipelineActionInput = { asOfDate: string; reason: string };

const observedLiveGenerations = new WeakMap<
  object,
  Map<string, ReadonlyMap<string, string>>
>();

const observedOfficialRuns = new WeakMap<
  object,
  Map<string, ReadonlyMap<string, string>>
>();

/** Cross-module current metrics + per-module generation signal, cheaply polled. */
export function useLiveSummary(bankId: string | undefined) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: scopedQueryKey('live-summary', scope, bankId ?? null),
    queryFn: () =>
      apiCall(() => liveEngineApi.getLiveSummary({ bankId: bankId! })),
    enabled: Boolean(bankId),
    refetchInterval: jitteredPollInterval(
      LIVE_SIGNAL_POLL_MS,
      'live-summary',
      scope,
      bankId,
    ),
  });

  useEffect(() => {
    if (!bankId || !query.data) return;
    let byScope = observedLiveGenerations.get(queryClient);
    if (!byScope) {
      byScope = new Map();
      observedLiveGenerations.set(queryClient, byScope);
    }
    const identity = `${scope.tenantId}|${scope.authorityId}|${bankId}`;
    const next = generationFingerprint(query.data.modules);
    const previous = byScope.get(identity);
    // Store first so duplicate useLiveSummary observers cannot invalidate the
    // same detail queries more than once for one generation transition.
    byScope.set(identity, next);
    if (!previous) return;
    const changed = changedGenerations(previous, next);
    if (changed.length > 0) {
      void invalidateGenerationChanges(queryClient, scope, bankId, changed);
    }
  }, [bankId, query.data, queryClient, scope]);

  return query;
}

/** Per-module live-vs-official-run freshness for a period, polled. */
export function useBankFreshness(
  bankId: string | undefined,
  periodId?: string | undefined,
  poll = true,
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: scopedQueryKey(
      'freshness',
      scope,
      bankId ?? null,
      periodId ?? null,
    ),
    queryFn: () =>
      apiCall(() =>
        liveEngineApi.getBankFreshness({
          bankId: bankId!,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId),
    refetchInterval: poll
      ? jitteredPollInterval(
          LIVE_SIGNAL_POLL_MS,
          'freshness',
          scope,
          bankId,
        )
      : false,
  });

  useEffect(() => {
    if (!bankId || !query.data) return;
    let byScope = observedOfficialRuns.get(queryClient);
    if (!byScope) {
      byScope = new Map();
      observedOfficialRuns.set(queryClient, byScope);
    }
    const identity = `${scope.tenantId}|${scope.authorityId}|${bankId}|${periodId ?? ''}`;
    const next = officialRunFingerprint(query.data.modules);
    const previous = byScope.get(identity);
    byScope.set(identity, next);
    if (!previous) return;
    const changed = changedGenerations(previous, next);
    if (changed.length > 0) {
      void invalidateOfficialRunChanges(
        queryClient,
        scope,
        bankId,
        changed,
      );
    }
  }, [bankId, periodId, query.data, queryClient, scope]);

  return query;
}

/** Open limit-breach alerts across modules, polled — powers the header bell. */
export function useBankAlerts(bankId: string | undefined, limit = 20) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey('alerts', scope, bankId ?? null, limit),
    queryFn: () =>
      apiCall(() =>
        liveEngineApi.getBankAlerts({ bankId: bankId!, limit })
      ),
    enabled: Boolean(bankId),
    refetchInterval: jitteredPollInterval(
      LIVE_SIGNAL_POLL_MS,
      'alerts',
      scope,
      bankId,
    ),
  });
}

const livePipelineCompletionPrefixes = [
  'freshness',
  'alerts',
  'bsd3',
  'bsd2',
  'reg-runs',
  'reg-run',
  'facts',
  'periods',
];

async function invalidateCompletedPipeline(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
) {
  await invalidateScopedPrefixes(queryClient, ['live-summary'], scope, bankId);
  await invalidateScopedPrefixes(
    queryClient,
    livePipelineCompletionPrefixes,
    scope,
    bankId,
  );
}

const officialRunCompletionPrefixes = regulatoryDetailInvalidationPrefixes([
  'liquidity',
  'capital',
  'irr',
  'fx',
  'ftp',
  'forecast',
]);

const capitalAssumptionPrefixes = regulatoryDetailInvalidationPrefixes([
  'capital',
  'forecast',
]);

async function invalidateCompletedOfficialRun(
  queryClient: QueryClient,
  scope: QueryAuthorityScope,
  bankId: string | undefined,
) {
  await invalidateCompletedPipeline(queryClient, scope, bankId);
  await invalidateScopedPrefixes(
    queryClient,
    officialRunCompletionPrefixes,
    scope,
    bankId,
  );
}

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
  const scope = useQueryAuthorityScope();
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
    onSuccess: () => invalidateCompletedPipeline(queryClient, scope, bankId),
  });
}

/**
 * "Mint official run for filing" — enqueue an immutable official run, poll it to
 * completion, and refresh every live read + module dashboard. The official run
 * is what clears the freshness "data changed since last official run" state.
 */
export function useMintOfficialRun(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
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
    onSuccess: () => invalidateCompletedOfficialRun(queryClient, scope, bankId),
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

// ---------------------------------------------------------------------------
// Per-bank market data overlays (spec §9): the bank's private spread layer on
// the published golden copy. Mutations invalidate both the overlay list and
// the composed views (adjusted curves are computed server-side at read time).
// ---------------------------------------------------------------------------

const overlayInvalidatePrefixes = ['md-overlays', 'md-views'];

export function useMarketDataOverlays(
  bankId: string | undefined,
  options?: { includeHistory?: boolean; baseCurveName?: string }
) {
  return useQuery({
    queryKey: [
      'md-overlays',
      bankId,
      options?.includeHistory ?? false,
      options?.baseCurveName ?? null,
    ],
    queryFn: () =>
      apiCall(() =>
        marketDataApi.listMarketDataOverlays({
          bankId: bankId!,
          includeHistory: options?.includeHistory,
          baseCurveName: options?.baseCurveName,
        })
      ),
    enabled: Boolean(bankId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useCreateMarketDataOverlay(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: MarketDataOverlayCreate) =>
      apiCall(() =>
        marketDataApi.createMarketDataOverlay({
          bankId: bankId!,
          marketDataOverlayCreate: payload,
        })
      ),
    onSuccess: () => {
      overlayInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

export function useEndMarketDataOverlay(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: { overlayId: string; effectiveTo: string }) =>
      apiCall(() =>
        marketDataApi.endMarketDataOverlay({
          bankId: bankId!,
          overlayId: payload.overlayId,
          marketDataOverlayEnd: {
            effectiveTo: new Date(`${payload.effectiveTo}T00:00:00Z`),
          },
        })
      ),
    onSuccess: () => {
      overlayInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

// ---------------------------------------------------------------------------
// Market-data SOURCE SELECTION (docs/internal/market_data_sources.md §4).
// The three-plane control room: per-bank source preference (which plane feeds
// the engines), the side-by-side plane comparison, and the desk's published
// forward grid. These call the hand-rolled fetch layer in ./marketDataSources
// (the endpoints post-date the last OpenAPI regeneration); the fetchers already
// throw ApiError, so no apiCall() wrapper is needed. A preference change
// invalidates the served views — the whole Markets tab re-renders on the newly
// selected plane, exactly as the engines will consume it.
// ---------------------------------------------------------------------------

const sourcePrefsInvalidatePrefixes = ['md-source-prefs', 'md-planes', 'md-views'];

export function useMarketDataSourcePreferences(bankId: string | undefined) {
  return useQuery({
    queryKey: ['md-source-prefs', bankId],
    queryFn: () => getMarketDataSourcePreferences(bankId!),
    enabled: Boolean(bankId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useUpdateMarketDataSourcePreferences(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: MarketDataSourcePreferencesPatch) =>
      putMarketDataSourcePreferences(bankId!, patch),
    onSuccess: (resolved) => {
      queryClient.setQueryData(['md-source-prefs', bankId], resolved);
      sourcePrefsInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * Side-by-side plane comparison for one category at the as-of date. Gated by
 * `enabled` so the three category queries only fire while the Sources tab is
 * open. Omit `asOf` for the live latest view.
 */
export function useMarketDataPlanes(
  bankId: string | undefined,
  category: MarketDataCategory,
  asOf?: string,
  enabled = true
) {
  return useQuery({
    queryKey: ['md-planes', bankId, category, asOf ?? null],
    queryFn: () => getMarketDataPlanes(bankId!, { category, asOf }),
    enabled: Boolean(bankId) && enabled,
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

/**
 * The desk's published forward grid for one curve (spec §4 FC-5/G1). DF and
 * forward yields are decimal fractions. Gated by `enabled` + a curve name so it
 * only fires once the Forward tab has a curve selected.
 */
export function useForwardGrid(
  bankId: string | undefined,
  curveName: string | null,
  asOf?: string,
  frequency?: string,
  enabled = true
) {
  return useQuery({
    queryKey: ['md-forward-grid', bankId, curveName, asOf ?? null, frequency ?? null],
    queryFn: () => getForwardGrid(bankId!, curveName!, { asOf, frequency }),
    enabled: Boolean(bankId) && Boolean(curveName) && enabled,
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
  const scope = useQueryAuthorityScope();
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
      void invalidateScopedPrefixes(
        queryClient,
        ['de-batches', 'de-summary'],
        scope,
        bankId,
      );
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
// ['rr-artifacts', bankId, packageId] (persisted artifact list from the API),
// ['rr-resubmissions', bankId, packageId], ['org-users']. Package mutations
// invalidate obligations + package reads.
// ---------------------------------------------------------------------------

const reportingInvalidatePrefixes = [
  'rr-obligations',
  'rr-packages',
  'rr-package',
  'rr-events',
  // Certification appends a signed revision, so the version chain — and with it
  // which document Download resolves to — changes without the artifact list
  // moving at all.
  'rr-artifact-versions',
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
  returnFamily?: string;
  /** ISO date (YYYY-MM-DD). */
  reportingDate?: string;
  /** Inclusive ISO date range bounds (YYYY-MM-DD). */
  reportingDateFrom?: string;
  reportingDateTo?: string;
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
      filters.returnFamily ?? null,
      filters.reportingDate ?? null,
      filters.reportingDateFrom ?? null,
      filters.reportingDateTo ?? null,
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
          returnFamily: filters.returnFamily,
          reportingDate: filters.reportingDate
            ? new Date(`${filters.reportingDate}T00:00:00Z`)
            : undefined,
          reportingDateFrom: filters.reportingDateFrom
            ? new Date(`${filters.reportingDateFrom}T00:00:00Z`)
            : undefined,
          reportingDateTo: filters.reportingDateTo
            ? new Date(`${filters.reportingDateTo}T00:00:00Z`)
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
 * Maker-checker decision, attributed to the authenticated checker (from the
 * verified token) — you cannot approve "as" another user. Deciding as the
 * generator returns the backend's maker-checker 409, surfaced verbatim.
 */
export function useDecidePackageApproval(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      action,
      reason,
    }: {
      packageId: string;
      action: ApprovalDecision;
      reason?: string;
    }) =>
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

/** Persisted artifact list for one package (survives reloads — API-backed). */
export function usePackageArtifacts(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['rr-artifacts', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listPackageArtifacts({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/**
 * The append-only version chain for one package: the base export, then one
 * archived revision per officer signature.
 *
 * Distinct from `usePackageArtifacts`, which reads the row that is upserted per
 * kind and therefore always names the UNSIGNED export. Once a return is
 * certified, the version flagged `isFiled` is the document — the one the
 * regulator receives — and the base export is retained only as the
 * pre-signature engine output.
 */
export function usePackageArtifactVersions(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['rr-artifact-versions', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listPackageArtifactVersions({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/**
 * The supersession chain, each version with its signatures, its files, and
 * whether it has any file at all.
 *
 * `useRegulatoryPackages` already lists the versions, but only their statuses
 * and timestamps — which cannot answer what is asked about a superseded
 * filing. This is what the Prior-versions card renders.
 */
export function usePackageVersionChain(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['rr-version-chain', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.getPackageVersionChain({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/**
 * The server-computed figures diff between two versions of one return.
 *
 * Fetched only once an operator opens a comparison (`enabled`), and never
 * derived client-side: the diff an examiner is shown has to be the one the
 * platform computed and can stand behind.
 */
export function useComparePackageVersions(
  bankId: string | undefined,
  packageId: string | null | undefined,
  againstPackageId: string | null | undefined,
  enabled = true
) {
  return useQuery({
    queryKey: ['rr-comparison', bankId, packageId, againstPackageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.comparePackageVersions({
          bankId: bankId!,
          packageId: packageId!,
          against: againstPackageId!,
        })
      ),
    enabled: Boolean(enabled && bankId && packageId && againstPackageId),
  });
}

/**
 * The Reports "Compare" line diff — two run versions of one period, or one
 * return across two periods. Calls the hand-rolled fetch layer in
 * ./reportComparison (the endpoint post-dates the last OpenAPI regeneration);
 * the fetcher already throws ApiError, so no apiCall() wrapper is needed.
 *
 * Fired only once both sides are chosen and distinct (`enabled`): a diff of a
 * thing against itself is meaningless, and the caller uses the disabled state to
 * show the "need two to compare" guidance instead.
 */
export function useReportComparison(
  bankId: string | undefined,
  params: ReportComparisonParams,
  enabled = true
) {
  return useQuery({
    queryKey: [
      'report-comparison',
      bankId,
      params.mode,
      params.module,
      params.left,
      params.right,
      params.scenarioCode ?? 'baseline',
    ],
    queryFn: () => getReportComparison(bankId!, params),
    enabled:
      Boolean(bankId && params.left && params.right) &&
      params.left !== params.right &&
      enabled,
  });
}

/** Mint one export artifact (xlsx/csv/pdf); refreshes the persisted list. */
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
      void queryClient.invalidateQueries({
        queryKey: ['rr-artifacts', bankId, artifact.packageId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['rr-artifact-versions', bankId, artifact.packageId],
      });
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

/** ORASS-parity resubmission requests filed against one package. */
export function useResubmissionRequests(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['rr-resubmissions', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listResubmissionRequests({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/** File a resubmission request for a submitted/acknowledged package. */
export function useRequestResubmission(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ packageId, reason }: { packageId: string; reason: string }) =>
      apiCall(() =>
        regulatoryReportingApi.requestPackageResubmission({
          bankId: bankId!,
          packageId,
          resubmissionRequestCreate: { reason },
        })
      ),
    onSuccess: (request) => {
      void queryClient.invalidateQueries({
        queryKey: ['rr-resubmissions', bankId, request.packageId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['rr-package', bankId, request.packageId],
      });
      void queryClient.invalidateQueries({ queryKey: ['rr-packages'] });
      void queryClient.invalidateQueries({ queryKey: ['rr-events'] });
    },
  });
}

/**
 * Record a manual grant/deny for email/manual submissions the regulator
 * decides offline (ORASS-channel requests are decided by the portal).
 */
export function useDecideResubmission(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      requestId,
      decision,
      note,
    }: {
      packageId: string;
      requestId: string;
      decision: 'granted' | 'denied';
      note?: string;
    }) =>
      apiCall(() =>
        regulatoryReportingApi.decidePackageResubmission({
          bankId: bankId!,
          packageId,
          requestId,
          resubmissionDecisionCreate: { decision, note: note ?? null },
        })
      ),
    onSuccess: (request) => {
      void queryClient.invalidateQueries({
        queryKey: ['rr-resubmissions', bankId, request.packageId],
      });
      void queryClient.invalidateQueries({
        queryKey: ['rr-package', bankId, request.packageId],
      });
      void queryClient.invalidateQueries({ queryKey: ['rr-packages'] });
      void queryClient.invalidateQueries({ queryKey: ['rr-events'] });
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
/**
 * The reporting dates one return reports on — BoG's own anchors, from the
 * return definition. NOT the bank's ingested reporting periods: those are a
 * consequence of when data arrived, and selecting from them made every weekly
 * BoG deadline invisible unless a month happened to end on that Friday.
 * Each anchor carries whether a position has been computed for it.
 */
export function useReturnAnchors(
  bankId: string | undefined,
  returnCode: string | undefined,
  horizonMonths = 3
) {
  return useQuery({
    queryKey: ['rr-anchors', bankId, returnCode, horizonMonths],
    queryFn: () =>
      apiCall(() =>
        regulatoryReportingApi.listReturnAnchors({
          bankId: bankId!,
          returnCode: returnCode!,
          horizonMonths,
        })
      ),
    enabled: Boolean(bankId && returnCode),
  });
}

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

// ---------------------------------------------------------------------------
// Institution Profile register — the corporate master-data mirror behind the
// LRT return family: profile, related parties (+roles/shareholdings), outlets,
// products, licences, and name history.
//
// Query key: ['institution-profile', bankId] — one composed read serves every
// register tab, so every mutation invalidates that single key. All mutation
// payloads carry a required non-empty `reason` (canonical-mutation convention).
// ---------------------------------------------------------------------------

/** The composed corporate register (profile may be null until first configured). */
export function useInstitutionProfile(bankId: string | undefined) {
  return useQuery({
    queryKey: ['institution-profile', bankId],
    queryFn: () =>
      apiCall(() =>
        institutionProfileApi.getInstitutionProfile({ bankId: bankId! })
      ),
    enabled: Boolean(bankId),
  });
}

/** Shared invalidation: every register mutation refreshes the composed read. */
function useInstitutionRegisterMutation<TVariables, TData>(
  bankId: string | undefined,
  mutationFn: (variables: TVariables) => Promise<TData>
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: ['institution-profile', bankId],
      });
    },
  });
}

/** Create-or-replace the corporate profile (PUT upsert). */
export function useSaveInstitutionProfile(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    (payload: InstitutionProfilePut) =>
      apiCall(() =>
        institutionProfileApi.putInstitutionProfile({
          bankId: bankId!,
          institutionProfilePut: payload,
        })
      )
  );
}

export function useCreateRelatedParty(bankId: string | undefined) {
  return useInstitutionRegisterMutation(bankId, (payload: RelatedPartyCreate) =>
    apiCall(() =>
      institutionProfileApi.createRelatedParty({
        bankId: bankId!,
        relatedPartyCreate: payload,
      })
    )
  );
}

/** Full replacement; the `roles` list is replace-on-write. */
export function useUpdateRelatedParty(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({ partyId, payload }: { partyId: string; payload: RelatedPartyUpdate }) =>
      apiCall(() =>
        institutionProfileApi.updateRelatedParty({
          bankId: bankId!,
          partyId,
          relatedPartyUpdate: payload,
        })
      )
  );
}

export function useCreateShareholding(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({ partyId, payload }: { partyId: string; payload: ShareholdingCreate }) =>
      apiCall(() =>
        institutionProfileApi.createShareholding({
          bankId: bankId!,
          partyId,
          shareholdingCreate: payload,
        })
      )
  );
}

export function useUpdateShareholding(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({
      partyId,
      shareholdingId,
      payload,
    }: {
      partyId: string;
      shareholdingId: string;
      payload: ShareholdingUpdate;
    }) =>
      apiCall(() =>
        institutionProfileApi.updateShareholding({
          bankId: bankId!,
          partyId,
          shareholdingId,
          shareholdingUpdate: payload,
        })
      )
  );
}

export function useCreateOutlet(bankId: string | undefined) {
  return useInstitutionRegisterMutation(bankId, (payload: OutletCreate) =>
    apiCall(() =>
      institutionProfileApi.createOutlet({
        bankId: bankId!,
        outletCreate: payload,
      })
    )
  );
}

/** Full replacement; closing (status='closed') stamps `closed_on`. */
export function useUpdateOutlet(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({ outletId, payload }: { outletId: string; payload: OutletUpdate }) =>
      apiCall(() =>
        institutionProfileApi.updateOutlet({
          bankId: bankId!,
          outletId,
          outletUpdate: payload,
        })
      )
  );
}

export function useCreateBankProduct(bankId: string | undefined) {
  return useInstitutionRegisterMutation(bankId, (payload: BankProductCreate) =>
    apiCall(() =>
      institutionProfileApi.createBankProduct({
        bankId: bankId!,
        bankProductCreate: payload,
      })
    )
  );
}

export function useUpdateBankProduct(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({ productId, payload }: { productId: string; payload: BankProductUpdate }) =>
      apiCall(() =>
        institutionProfileApi.updateBankProduct({
          bankId: bankId!,
          productId,
          bankProductUpdate: payload,
        })
      )
  );
}

export function useCreateBankLicense(bankId: string | undefined) {
  return useInstitutionRegisterMutation(bankId, (payload: BankLicenseCreate) =>
    apiCall(() =>
      institutionProfileApi.createBankLicense({
        bankId: bankId!,
        bankLicenseCreate: payload,
      })
    )
  );
}

export function useUpdateBankLicense(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({ licenseId, payload }: { licenseId: string; payload: BankLicenseUpdate }) =>
      apiCall(() =>
        institutionProfileApi.updateBankLicense({
          bankId: bankId!,
          licenseId,
          bankLicenseUpdate: payload,
        })
      )
  );
}

export function useCreateNameHistoryEntry(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    (payload: BankNameHistoryCreate) =>
      apiCall(() =>
        institutionProfileApi.createNameHistoryEntry({
          bankId: bankId!,
          bankNameHistoryCreate: payload,
        })
      )
  );
}

export function useUpdateNameHistoryEntry(bankId: string | undefined) {
  return useInstitutionRegisterMutation(
    bankId,
    ({ entryId, payload }: { entryId: string; payload: BankNameHistoryUpdate }) =>
      apiCall(() =>
        institutionProfileApi.updateNameHistoryEntry({
          bankId: bankId!,
          entryId,
          bankNameHistoryUpdate: payload,
        })
      )
  );
}

// ---------------------------------------------------------------------------
// Organization directory
// ---------------------------------------------------------------------------

/** The tenant's user roster — display names for actor-id attribution. */
export function useOrganizationUsers() {
  return useQuery({
    queryKey: ['org-users'],
    queryFn: () => apiCall(() => organizationApi.listOrganizationUsers()),
    staleTime: 10 * 60_000,
  });
}

/**
 * Resolve actor user ids to "Display Name (Role)" via the organization
 * roster; unknown ids fall back to the 8-char id prefix.
 */
export function useOfficerNames(): (userId: string) => string {
  const usersQuery = useOrganizationUsers();
  const users = usersQuery.data?.users;
  return useCallback(
    (userId: string) => {
      const user = users?.find((entry) => entry.id === userId);
      if (!user) return userId.slice(0, 8);
      const name = user.displayName ?? user.email;
      const role = user.jobTitle ?? user.role;
      return role ? `${name} (${role})` : name;
    },
    [users]
  );
}

// ---------------------------------------------------------------------------
// Notifications (in-app feed; emitted by the reporting workflow + deadline scan)
// Query keys: ['notifications', unreadOnly]
// ---------------------------------------------------------------------------

/** The actor-visible notification feed (user-directed + org-wide rows). */
export function useNotifications(unreadOnly = false) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: scopedQueryKey('notifications', scope, unreadOnly),
    queryFn: () =>
      apiCall(() =>
        notificationsApi.listNotifications({ unreadOnly, limit: 50 })
      ),
    refetchInterval: jitteredPollInterval(
      60_000,
      'notifications',
      scope,
    ),
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (notificationId: string) =>
      apiCall(() => notificationsApi.markNotificationRead({ notificationId })),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiCall(() => notificationsApi.markAllNotificationsRead()),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['notifications'] });
    },
  });
}

// ---------------------------------------------------------------------------
// Integration keys — generate-once revocable API credentials for bank
// middleware (admin-only surface; the raw key is returned exactly once).
// ---------------------------------------------------------------------------

export function useIntegrationKeys(enabled: boolean) {
  return useQuery({
    queryKey: ['integration-keys'],
    queryFn: () => apiCall(() => integrationKeysApi.listIntegrationKeys()),
    enabled,
  });
}

export function useIssueIntegrationKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (label: string) =>
      apiCall(() =>
        integrationKeysApi.issueIntegrationKey({
          integrationKeyIssueRequest: { label },
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['integration-keys'] });
    },
  });
}

export function useRevokeIntegrationKey() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ keyId, reason }: { keyId: string; reason: string }) =>
      apiCall(() =>
        integrationKeysApi.revokeIntegrationKey({
          keyId,
          integrationKeyRevokeRequest: { reason },
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['integration-keys'] });
    },
  });
}

// ---------------------------------------------------------------------------
// Attestation & e-signature (docs/attestation_esignature.md §4.6).
//
// Query keys: ['attn-identity'], ['attn-status', bankId, packageId],
// ['attn-preview', bankId, packageId, role], ['attn-verify', bankId, packageId],
// ['attn-policies'].
//
// Certifying and voiding move the PACKAGE status too (validated →
// pending_approval at T1, pending_approval → approved at T2, back to generated
// on void), so those mutations invalidate the reporting reads as well as the
// attestation ones — otherwise the workspace would show a stale lifecycle.
// ---------------------------------------------------------------------------

/** The caller's own permanent signer identity (provisioned on first read). */
export function useMySignerIdentity(enabled = true) {
  return useQuery({
    queryKey: ['attn-identity'],
    queryFn: () => apiCall(() => attestationApi.getMySignerIdentity()),
    enabled,
    // Permanent by design (§2.3) — no reason to re-fetch it on every mount.
    staleTime: 30 * 60_000,
  });
}

/** Who has signed, who must still sign, and whether submission is unlocked. */
export function usePackageAttestation(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['attn-status', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        attestationApi.getPackageAttestation({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/**
 * Exactly what will be signed. Never cached beyond the open ceremony: the
 * digest the signer is shown is compared server-side at certify time, so a
 * stale preview must surface as a refused signature, not a silent one.
 */
export function useCertificationPreview(
  bankId: string | undefined,
  packageId: string | null | undefined,
  signingRole: SigningRole,
  enabled = true
) {
  return useQuery({
    queryKey: ['attn-preview', bankId, packageId, signingRole],
    queryFn: () =>
      apiCall(() =>
        attestationApi.previewCertification({
          bankId: bankId!,
          packageId: packageId!,
          signingRole,
        })
      ),
    enabled: Boolean(bankId && packageId) && enabled,
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });
}

/**
 * Step 1 of the ceremony: prove presence now, receive a single-use
 * authorisation bound to (user, package, digest, role). Deliberately not
 * cached and never retried — a re-authentication attempt is not idempotent.
 */
export function useStepUpForSigning(bankId: string | undefined) {
  return useMutation({
    mutationFn: ({
      packageId,
      signingRole,
      password,
      idToken,
    }: {
      packageId: string;
      signingRole: SigningRole;
      password?: string;
      idToken?: string;
    }) =>
      apiCall(() =>
        attestationApi.stepUpForSigning({
          bankId: bankId!,
          packageId,
          stepUpRequest: {
            signingRole,
            password: password ?? null,
            idToken: idToken ?? null,
          },
        })
      ),
  });
}

/**
 * Step 2: spend the authorisation and record the signature.
 * `expectedCertificationDigest` is the digest the browser rendered — the
 * backend compares it, so a stale tab cannot certify figures nobody read.
 */
export function useCertifyPackage(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      signingRole,
      authorizationToken,
      expectedCertificationDigest,
    }: {
      packageId: string;
      signingRole: SigningRole;
      authorizationToken: string;
      expectedCertificationDigest: string;
    }) =>
      apiCall(() =>
        attestationApi.certifyPackage({
          bankId: bankId!,
          packageId,
          certifyRequest: {
            signingRole,
            authorizationToken,
            expectedCertificationDigest,
          },
        })
      ),
    onSuccess: (status) => {
      queryClient.setQueryData(['attn-status', bankId, status.packageId], status);
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-verify'] });
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * Step 2, SSO variant: the authorisation was minted by the step-up callback and
 * lives in an HttpOnly cookie, so it cannot be read here. The Next.js route
 * `/api/attestation/certify` reads and spends it server-side and relays the risk
 * service's verdict verbatim — including `details.error_code`, so the dialog
 * branches on the same codes as the password path.
 */
export function useCertifyWithHeldAuthorization(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      packageId,
      signingRole,
      expectedCertificationDigest,
    }: {
      packageId: string;
      signingRole: SigningRole;
      expectedCertificationDigest: string;
    }) => {
      const response = await fetch('/api/attestation/certify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bankId: bankId!,
          packageId,
          signingRole,
          expectedCertificationDigest,
        }),
      });
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const envelope = (body as { error?: unknown })?.error ?? body;
        const shaped = (
          envelope && typeof envelope === 'object' ? envelope : {}
        ) as { code?: string; message?: string; details?: unknown };
        const details = shaped.details as { error_code?: string; message?: string } | null;
        throw new ApiError({
          message:
            details?.message ??
            shaped.message ??
            `Certification failed (${response.status}).`,
          status: response.status,
          code: shaped.code ?? null,
          errorCode: details?.error_code ?? null,
          details: shaped.details ?? null,
        });
      }
      return body as AttestationStatusRead;
    },
    onSuccess: (status) => {
      queryClient.setQueryData(['attn-status', bankId, status.packageId], status);
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-verify'] });
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/** Withdraw the current attestation. Signatures are retained, never deleted. */
export function useVoidAttestation(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ packageId, reason }: { packageId: string; reason: string }) =>
      apiCall(() =>
        attestationApi.voidAttestation({
          bankId: bankId!,
          packageId,
          voidAttestationRequest: { reason },
        })
      ),
    onSuccess: (status) => {
      queryClient.setQueryData(['attn-status', bankId, status.packageId], status);
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-verify'] });
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * The reviewing approver's other exit: return the package with a note.
 *
 * One call, because the backend records the rejected decision AND withdraws the
 * certification that froze the figures in one transaction — a return sent back
 * with its figures still frozen is a return nobody can correct.
 */
export function useSendBackForCorrections(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ packageId, reason }: { packageId: string; reason: string }) =>
      apiCall(() =>
        attestationApi.sendPackageBackForCorrections({
          bankId: bankId!,
          packageId,
          sendBackForCorrectionsRequest: { reason },
        })
      ),
    onSuccess: (status) => {
      queryClient.setQueryData(['attn-status', bankId, status.packageId], status);
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-verify'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-awaiting'] });
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * The five independent verification checks (§3.5). Opt-in rather than always-on:
 * verification re-hashes artifacts and re-validates certificate chains, so it
 * runs when a human asks for it.
 */
export function useVerifyPackageAttestation(
  bankId: string | undefined,
  packageId: string | null | undefined,
  enabled: boolean
) {
  return useQuery({
    queryKey: ['attn-verify', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        attestationApi.verifyPackageAttestation({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId) && enabled,
    staleTime: 0,
    retry: false,
  });
}

// ---------------------------------------------------------------------------
// The signing workspace: field placement, adopted marks, named routing.
//
// Query keys: ['attn-placements', bankId, packageId], ['attn-appearance'],
// ['attn-awaiting'].
// ---------------------------------------------------------------------------

/**
 * Where this return's signature fields will be created, and from which source
 * (package override → bank template → org template → platform default).
 * `editable` is false once a signature exists: the fields are part of the
 * certified revision and the DocMDP policy forbids moving one afterwards.
 */
export function usePackageSignaturePlacements(
  bankId: string | undefined,
  packageId: string | null | undefined
) {
  return useQuery({
    queryKey: ['attn-placements', bankId, packageId],
    queryFn: () =>
      apiCall(() =>
        attestationApi.getPackageSignaturePlacements({
          bankId: bankId!,
          packageId: packageId!,
        })
      ),
    enabled: Boolean(bankId && packageId),
  });
}

/**
 * Persist this package's field boxes. Reason-required and audited, because the
 * placement decides where an officer's name and permanent signer ID appear on a
 * document filed with the regulator.
 *
 * "Certify and send" carries the placement with it, so this is only used when
 * the boxes have to survive something — the SSO redirect, where the browser
 * that holds them is about to be navigated away.
 */
export function useSetPackageSignaturePlacements(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      placements,
      reason,
    }: {
      packageId: string;
      placements: SignatureFieldPlacement[];
      reason: string;
    }) =>
      apiCall(() =>
        attestationApi.setPackageSignaturePlacements({
          bankId: bankId!,
          packageId,
          packageSignaturePlacementRequest: { placements, reason },
        })
      ),
    onSuccess: (resolved) => {
      queryClient.setQueryData(
        ['attn-placements', bankId, resolved.packageId],
        resolved
      );
    },
  });
}

/**
 * Save this layout as the reusable template for a return code (optionally for
 * one bank), so next month's filing opens with the boxes already on the lines.
 *
 * Admin-only server-side, like every other placement template write: the
 * template decides where an officer's name and permanent signer ID print on
 * every future filing of that return, not just this one.
 */
export function useUpsertSignaturePlacementTemplate() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SignaturePlacementTemplateUpsertRequest) =>
      apiCall(() =>
        attestationApi.upsertSignaturePlacementTemplate({
          signaturePlacementTemplateUpsertRequest: payload,
        })
      ),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ['attn-placements'] }),
  });
}

/** The caller's own adopted mark — drawn or typed. Never anybody else's. */
export function useMyAdoptedSignature(enabled = true) {
  return useQuery({
    queryKey: ['attn-appearance'],
    queryFn: () => apiCall(() => attestationApi.getMyAdoptedSignature()),
    enabled,
  });
}

/** Adopt or re-adopt the mark. Drawn bytes are normalised server-side. */
export function useAdoptMySignature() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: AdoptSignatureRequest) =>
      apiCall(() =>
        attestationApi.adoptMySignature({ adoptSignatureRequest: payload })
      ),
    onSuccess: (adopted) => {
      queryClient.setQueryData(['attn-appearance'], adopted);
    },
  });
}

/**
 * Certify AND name the remaining signers, in one transaction. A nominee the
 * policy cannot accept takes the certification down with it — the alternative
 * is a certified return sitting in nobody's queue.
 */
export function useCertifyAndSend(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      packageId,
      ...request
    }: CertifyAndSendRequest & { packageId: string }) =>
      apiCall(() =>
        attestationApi.certifyAndSendPackage({
          bankId: bankId!,
          packageId,
          certifyAndSendRequest: request,
        })
      ),
    onSuccess: (status) => {
      queryClient.setQueryData(['attn-status', bankId, status.packageId], status);
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-verify'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-placements'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-awaiting'] });
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * The same act on the SSO path. The authorisation was minted by the step-up
 * callback and lives in an HttpOnly cookie, so it cannot be read here — the
 * Next.js route `/api/attestation/certify-and-send` reads and spends it
 * server-side and relays the risk service's verdict verbatim, including
 * `details.error_code`, so this branches on the same codes as the password path.
 */
export function useCertifyAndSendWithHeldAuthorization(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      packageId,
      signingRole,
      expectedCertificationDigest,
      recipients,
      placements,
      reason,
    }: Omit<CertifyAndSendRequest, 'authorizationToken'> & { packageId: string }) => {
      const response = await fetch('/api/attestation/certify-and-send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          bankId: bankId!,
          packageId,
          signingRole,
          expectedCertificationDigest,
          recipients,
          placements,
          reason,
        }),
      });
      const body: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const envelope = (body as { error?: unknown })?.error ?? body;
        const shaped = (
          envelope && typeof envelope === 'object' ? envelope : {}
        ) as { code?: string; message?: string; details?: unknown };
        const details = shaped.details as { error_code?: string; message?: string } | null;
        throw new ApiError({
          message:
            details?.message ??
            shaped.message ??
            `Certification failed (${response.status}).`,
          status: response.status,
          code: shaped.code ?? null,
          errorCode: details?.error_code ?? null,
          details: shaped.details ?? null,
        });
      }
      return body as AttestationStatusRead;
    },
    onSuccess: (status) => {
      queryClient.setQueryData(['attn-status', bankId, status.packageId], status);
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-verify'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-placements'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-awaiting'] });
      reportingInvalidatePrefixes.forEach((prefix) => {
        void queryClient.invalidateQueries({ queryKey: [prefix] });
      });
    },
  });
}

/**
 * Returns routed to the caller and still unsigned. Polled, because a signature
 * request arrives from somebody else's action rather than from anything this
 * browser did.
 */
export function useReturnsAwaitingMySignature(enabled = true) {
  return useQuery({
    queryKey: ['attn-awaiting'],
    queryFn: () => apiCall(() => attestationApi.listReturnsAwaitingMySignature()),
    enabled,
    refetchInterval: 60_000,
  });
}

/** Configured signing policies for the org (the built-in default is not a row). */
export function useSigningPolicies(enabled = true) {
  return useQuery({
    queryKey: ['attn-policies'],
    queryFn: () => apiCall(() => attestationApi.listSigningPolicies()),
    enabled,
  });
}

/**
 * Create or supersede a signing policy. Reason-required and admin-only: this is
 * the control that decides whether a filed return is properly attested, so
 * changing it is itself an audited act. Policies are versioned by effective
 * date rather than edited in place, so every attestation read invalidates too.
 */
export function useUpsertSigningPolicy() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: PolicyUpsertRequest) =>
      apiCall(() =>
        attestationApi.upsertSigningPolicy({ policyUpsertRequest: payload })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['attn-policies'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-status'] });
      void queryClient.invalidateQueries({ queryKey: ['attn-preview'] });
    },
  });
}

// --- Phase 2: EWI / CFP / threshold registers / reverse stress -------------

export function useEwiDashboard(bankId: string | undefined, periodId?: string | undefined) {
  return useQuery({
    queryKey: ['ewi-dashboard', bankId, periodId],
    queryFn: () =>
      apiCall(async () => {
        // The EWI service uses a valid HTTP 200 availability envelope while
        // current liquidity facts are unavailable. Inspect it before generated
        // deserialization expects the dashboard's indicator array.
        const response = await liquidityCfpApi.getLiquidityEwiDashboardRaw({
          bankId: bankId!,
          reportingPeriodId: periodId,
        });
        const payload = (await response.raw.clone().json()) as {
          available?: boolean;
          error_code?: string;
          reason?: string;
        };
        if (payload.available === false && payload.error_code) {
          throw new ModuleUnavailableError(
            payload.reason ?? 'Early-warning indicators are not available yet.',
            payload.error_code
          );
        }
        return response.value();
      }),
    enabled: Boolean(bankId),
    refetchInterval: DASHBOARD_REFETCH_MS,
  });
}

export function useCfpSummary(bankId: string | undefined) {
  return useQuery({
    queryKey: ['cfp-summary', bankId],
    queryFn: () => apiCall(() => liquidityCfpApi.getContingencyFundingPlan({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useCfpEvents(bankId: string | undefined) {
  return useQuery({
    queryKey: ['cfp-events', bankId],
    queryFn: () =>
      apiCall(() => liquidityCfpApi.listContingencyFundingPlanEvents({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useLiquidityThresholdRegister(bankId: string | undefined) {
  return useQuery({
    queryKey: ['liq-thresholds', bankId],
    queryFn: () =>
      apiCall(() => liquidityThresholdsApi.getLiquidityThresholdRegister({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useLiquidityHaircutSchedule(bankId: string | undefined) {
  return useQuery({
    queryKey: ['liq-haircuts', bankId],
    queryFn: () =>
      apiCall(() => liquidityThresholdsApi.getLiquidityHaircutSchedule({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useLatestReverseStress(
  bankId: string | undefined,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['reverse-stress', bankId, periodId],
    queryFn: async () => {
      try {
        return await apiCall(() =>
          reverseStressApi.getLatestReverseStress({
            bankId: bankId!,
            reportingPeriodId: periodId!,
          })
        );
      } catch (error) {
        // No frontier run yet is a normal state, not an error banner.
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(bankId && periodId),
  });
}

export function useRunReverseStress(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (periodId: string) =>
      apiCall(() =>
        reverseStressApi.runReverseStress({
          bankId: bankId!,
          reverseStressRunCreate: { reportingPeriodId: periodId },
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['reverse-stress'] });
    },
  });
}

// --- Scenario Workbench ------------------------------------------------------

export function useScenarioCatalogue(
  bankId: string | undefined,
  module: WorkbenchModule,
  periodId: string | undefined
) {
  return useQuery({
    queryKey: ['scenario-catalogue', bankId, module, periodId],
    queryFn: () =>
      apiCall(() =>
        scenarioWorkbenchApi.listScenarioCatalogue({
          bankId: bankId!,
          module,
          reportingPeriodId: periodId,
        })
      ),
    enabled: Boolean(bankId),
  });
}

export function useRunScenarioAnalysis(bankId: string | undefined, module: WorkbenchModule) {
  return useMutation({
    mutationFn: (payload: AnalysisRunCreate) =>
      apiCall(() =>
        scenarioWorkbenchApi.runScenarioAnalysis({
          bankId: bankId!,
          module,
          analysisRunCreate: payload,
        })
      ),
  });
}

export function useCreateStressScenario(bankId: string | undefined, module: WorkbenchModule) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: StressScenarioCreate) =>
      apiCall(() =>
        scenarioWorkbenchApi.createStressScenario({
          bankId: bankId!,
          module,
          stressScenarioCreate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['scenario-catalogue', bankId, module] });
    },
  });
}

export function useUpdateStressScenario(bankId: string | undefined, module: WorkbenchModule) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ scenarioId, payload }: { scenarioId: string; payload: StressScenarioUpdate }) =>
      apiCall(() =>
        scenarioWorkbenchApi.updateStressScenario({
          bankId: bankId!,
          module,
          scenarioId,
          stressScenarioUpdate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['scenario-catalogue', bankId, module] });
    },
  });
}

export function useArchiveStressScenario(bankId: string | undefined, module: WorkbenchModule) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ scenarioId, isArchived }: { scenarioId: string; isArchived: boolean }) =>
      apiCall(() =>
        scenarioWorkbenchApi.archiveStressScenario({
          bankId: bankId!,
          module,
          scenarioId,
          stressScenarioArchive: { isArchived },
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['scenario-catalogue', bankId, module] });
    },
  });
}

export function useSavedAnalyses(bankId: string | undefined, module: WorkbenchModule) {
  return useQuery({
    queryKey: ['scenario-analyses', bankId, module],
    queryFn: () =>
      apiCall(() => scenarioWorkbenchApi.listScenarioAnalyses({ bankId: bankId!, module })),
    enabled: Boolean(bankId),
  });
}

export function useSaveScenarioAnalysis(bankId: string | undefined, module: WorkbenchModule) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: SavedAnalysisCreate) =>
      apiCall(() =>
        scenarioWorkbenchApi.saveScenarioAnalysis({
          bankId: bankId!,
          module,
          savedAnalysisCreate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['scenario-analyses', bankId, module] });
    },
  });
}

export function useDeleteScenarioAnalysis(bankId: string | undefined, module: WorkbenchModule) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (analysisId: string) =>
      apiCall(() =>
        scenarioWorkbenchApi.deleteScenarioAnalysis({ bankId: bankId!, module, analysisId })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['scenario-analyses', bankId, module] });
    },
  });
}


// --- Board registers (Governance → Board Registers editor surface) ---------
// Reads pair with the existing register hooks above (['liq-thresholds'],
// ['ewi-dashboard']); the credit-parameter registers get their own keys here.
// Every PUT is approver-gated server-side and audited — the payloads carry
// the Board evidence (approved_by + reason), never a bare value change.

export function useCrmHaircutRegister(bankId: string | undefined) {
  return useQuery({
    queryKey: ['crm-haircuts', bankId],
    queryFn: () => apiCall(() => creditParamsApi.getCrmHaircutRegister({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useEclAssumptionRegister(bankId: string | undefined) {
  return useQuery({
    queryKey: ['ecl-assumptions', bankId],
    queryFn: () => apiCall(() => creditParamsApi.getEclAssumptionRegister({ bankId: bankId! })),
    enabled: Boolean(bankId),
  });
}

export function useUpdateLiquidityThresholdRegister(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (payload: LiquidityThresholdUpdate) =>
      apiCall(() =>
        liquidityThresholdsApi.updateLiquidityThresholdRegister({
          bankId: bankId!,
          liquidityThresholdUpdate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['liq-thresholds', bankId] });
      // Threshold generations feed the monitoring/liquidity views.
      void invalidateScopedPrefixes(
        queryClient,
        ['liq-dashboard'],
        scope,
        bankId,
      );
    },
  });
}

export function useUpdateLiquidityEwiRegister(bankId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (payload: EwiRegisterPut) =>
      apiCall(() =>
        liquidityCfpApi.updateLiquidityEwiRegister({
          bankId: bankId!,
          ewiRegisterPut: payload,
        })
      ),
    onSuccess: () => {
      // Prefix-invalidates every period's dashboard read.
      void queryClient.invalidateQueries({ queryKey: ['ewi-dashboard', bankId] });
    },
  });
}

export function useUpdateCrmHaircutRegister(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (payload: CrmHaircutUpdate) =>
      apiCall(() =>
        creditParamsApi.updateCrmHaircutRegister({
          bankId: bankId!,
          crmHaircutUpdate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['crm-haircuts', bankId] });
      void invalidateScopedPrefixes(
        queryClient,
        capitalAssumptionPrefixes,
        scope,
        bankId,
      );
    },
  });
}

export function useUpdateEclAssumptionRegister(bankId: string | undefined) {
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (payload: EclAssumptionUpdate) =>
      apiCall(() =>
        creditParamsApi.updateEclAssumptionRegister({
          bankId: bankId!,
          eclAssumptionUpdate: payload,
        })
      ),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['ecl-assumptions', bankId] });
      void invalidateScopedPrefixes(
        queryClient,
        capitalAssumptionPrefixes,
        scope,
        bankId,
      );
    },
  });
}

/**
 * Plane-2 daily ladder for one bank+module: past days are EOD closes, the
 * newest row is the live edge. Powers prior-close deltas and daily sparklines.
 */
export function useLiveSnapshots(
  bankId: string | undefined,
  module: LiveModule,
  days = 45
) {
  const scope = useQueryAuthorityScope();
  const queryClient = useQueryClient();
  return useQuery({
    queryKey: scopedQueryKey(
      'live-snapshots',
      scope,
      bankId ?? null,
      module,
      days,
    ),
    queryFn: async () => {
      await waitForInitialDashboardSignals(queryClient, scope, bankId);
      return apiCall(() =>
        liveEngineApi.listLiveSnapshots({ bankId: bankId!, module, days })
      );
    },
    enabled: Boolean(bankId),
    ...HEAVY_DASHBOARD_QUERY_POLICY,
  });
}

/**
 * Engine-computed window analytics over [start, end] (ISO yyyy-mm-dd):
 * per-period ratio series (stored baseline runs first, inline fallback),
 * window statistics, and daily-snapshot aggregates. Fires only when enabled —
 * the Compute button on the Command Center gates it.
 */
export function useWindowAnalytics(
  bankId: string | undefined,
  start: string | undefined,
  end: string | undefined,
  enabled: boolean
) {
  return useQuery({
    queryKey: ['window-analytics', bankId, start ?? null, end ?? null],
    queryFn: () =>
      apiCall(() =>
        windowAnalyticsApi.computeWindowAnalytics({
          bankId: bankId!,
          startDate: new Date(start!),
          endDate: new Date(end!),
        })
      ),
    enabled: enabled && Boolean(bankId && start && end),
  });
}

/**
 * Contractual cash-flow window over [start, end] (ISO yyyy-mm-dd): the current
 * canonical book's maturities bucketed per currency and calendar month
 * server-side. Fires only when enabled — the panel's Compute button gates it.
 */
export function useCashflowWindow(
  bankId: string | undefined,
  start: string | undefined,
  end: string | undefined,
  enabled: boolean
) {
  return useQuery({
    queryKey: ['cashflow-window', bankId, start ?? null, end ?? null],
    queryFn: () =>
      apiCall(() =>
        cashflowWindowApi.computeCashflowWindow({
          bankId: bankId!,
          startDate: new Date(start!),
          endDate: new Date(end!),
        })
      ),
    enabled: enabled && Boolean(bankId && start && end),
  });
}
