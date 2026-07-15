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
  CashflowForecastMode,
  CashflowHorizon,
  ForecastRunCreate,
  RegulatoryModule,
  RegulatoryScenarioCode,
  WhatIfShockCode,
} from '@aequoros/risk-service-api';
import {
  apiCall,
  banksApi,
  cashflowForecastApi,
  forecastingApi,
  isApiError,
  regulatoryCapitalApi,
  regulatoryFtpApi,
  regulatoryFxApi,
  regulatoryIrrApi,
  regulatoryLiquidityApi,
  tenant,
} from './client';

const t = { xOrgId: tenant.orgId, xUserId: tenant.userId } as const;

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
