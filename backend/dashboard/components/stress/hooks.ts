'use client';

/**
 * Typed TanStack Query hooks for the Phase-1..5 stress surface (docs/stress.md):
 * governed macro scenarios, enterprise-wide stress runs (+ Appendix II) and the
 * management-action plan library.
 *
 * These endpoints are not yet in the generated OpenAPI client (regeneration is
 * owned by a concurrent effort, so `lib/api/hooks.ts` is off-limits here). The
 * hooks therefore issue authenticated `fetch`es directly and unwrap the backend
 * error envelope into the shared `ApiError`, mirroring `lib/api/client.ts` so
 * `QueryBoundary` / `ErrorPanel` render failures identically to the rest of the
 * app.
 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { getSession } from 'next-auth/react';
import { ApiError, apiBaseUrl } from '@/lib/api/client';
import { getAccessToken, setAccessToken } from '@/lib/api/token';
import type {
  AppendixIITables,
  EnterpriseProjection,
  EnterpriseStressRead,
  EnterpriseStressRunCreate,
  EnterpriseStressRunSummary,
  MacroScenario,
  MacroScenarioCreate,
  MacroScenarioList,
  ManagementActionPlan,
  ManagementActionPlanList,
  StressSignoffAttestation,
  StressSignoffCreate,
  StressSignoffListRead,
  StressSignoffRead,
  StressSignoffSummary,
} from './types';
import type { ScenarioStatus, ScenarioType } from './macro';

async function bearer(): Promise<string> {
  const cached = getAccessToken();
  if (cached) return cached;
  const session = await getSession();
  const token = session?.accessToken ?? '';
  if (token) setAccessToken(token);
  return token;
}

type FetchInit = {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | boolean | undefined>;
};

/** Authenticated JSON fetch that raises the shared `ApiError` on failure. */
async function authFetch<T>(path: string, init: FetchInit = {}): Promise<T> {
  const token = await bearer();
  const url = new URL(`${apiBaseUrl}${path}`);
  for (const [key, value] of Object.entries(init.query ?? {})) {
    if (value !== undefined) url.searchParams.set(key, String(value));
  }
  let response: Response;
  try {
    response = await fetch(url.toString(), {
      method: init.method ?? 'GET',
      headers: {
        'content-type': 'application/json',
        ...(token ? { authorization: `Bearer ${token}` } : {}),
      },
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch (error) {
    throw new ApiError({
      message: 'Could not reach the risk service. Check that the backend is running.',
      status: null,
      code: 'network_error',
      errorCode: null,
      details: error instanceof Error ? error.message : String(error),
    });
  }
  if (!response.ok) {
    let code: string | null = null;
    let errorCode: string | null = null;
    let message = `Request failed (${response.status}).`;
    let details: unknown;
    try {
      const body = await response.json();
      const envelope = body?.error ?? body;
      if (envelope && typeof envelope === 'object') {
        if (typeof envelope.code === 'string') code = envelope.code;
        if (typeof envelope.message === 'string') message = envelope.message;
        details = envelope.details ?? envelope.detail ?? null;
        const detailObj = details as { error_code?: string; message?: string } | null;
        if (detailObj && typeof detailObj === 'object') {
          if (typeof detailObj.error_code === 'string') errorCode = detailObj.error_code;
          if (typeof detailObj.message === 'string') message = detailObj.message;
        }
      }
    } catch {
      // Non-JSON body — keep the generic message.
    }
    throw new ApiError({ message, status: response.status, code, errorCode, details });
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// --- Macro scenario library --------------------------------------------------

const SCENARIO_KEY = 'stress-macro-scenarios';

export type MacroScenarioFilters = {
  bankId?: string;
  scenarioType?: ScenarioType;
  status?: ScenarioStatus;
  includeArchived?: boolean;
};

export function useMacroScenarios(filters: MacroScenarioFilters = {}) {
  return useQuery({
    queryKey: [SCENARIO_KEY, filters],
    queryFn: () =>
      authFetch<MacroScenarioList>('/macro-scenarios', {
        query: {
          bank_id: filters.bankId,
          scenario_type: filters.scenarioType,
          status: filters.status,
          include_archived: filters.includeArchived,
        },
      }),
  });
}

export function useMacroScenario(scenarioId: string | undefined) {
  return useQuery({
    queryKey: [SCENARIO_KEY, 'detail', scenarioId],
    queryFn: () => authFetch<MacroScenario>(`/macro-scenarios/${scenarioId}`),
    enabled: Boolean(scenarioId),
  });
}

export function useCreateMacroScenario() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: MacroScenarioCreate) =>
      authFetch<MacroScenario>('/macro-scenarios', { method: 'POST', body: payload }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [SCENARIO_KEY] }),
  });
}

export function useUpdateMacroScenario() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ scenarioId, payload }: { scenarioId: string; payload: Partial<MacroScenarioCreate> & { reason: string } }) =>
      authFetch<MacroScenario>(`/macro-scenarios/${scenarioId}`, { method: 'PATCH', body: payload }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [SCENARIO_KEY] }),
  });
}

function scenarioTransition(action: 'submit' | 'approve' | 'archive') {
  return function useScenarioTransition() {
    const qc = useQueryClient();
    return useMutation({
      mutationFn: ({ scenarioId, reason }: { scenarioId: string; reason: string }) =>
        authFetch<MacroScenario>(`/macro-scenarios/${scenarioId}/${action}`, {
          method: 'POST',
          body: { reason },
        }),
      onSuccess: () => void qc.invalidateQueries({ queryKey: [SCENARIO_KEY] }),
    });
  };
}

export const useSubmitMacroScenario = scenarioTransition('submit');
export const useApproveMacroScenario = scenarioTransition('approve');
export const useArchiveMacroScenario = scenarioTransition('archive');

// --- Management-action plan library -----------------------------------------

const PLAN_KEY = 'stress-management-plans';

export function useManagementActionPlans(bankId?: string) {
  return useQuery({
    queryKey: [PLAN_KEY, bankId],
    queryFn: () =>
      authFetch<ManagementActionPlanList>('/management-action-plans', {
        query: { bank_id: bankId },
      }),
  });
}

export function useManagementActionPlan(planId: string | undefined) {
  return useQuery({
    queryKey: [PLAN_KEY, 'detail', planId],
    queryFn: () => authFetch<ManagementActionPlan>(`/management-action-plans/${planId}`),
    enabled: Boolean(planId),
  });
}

// --- Enterprise-wide stress runs --------------------------------------------

const RUN_KEY = 'stress-enterprise-runs';

export function useRunEnterpriseStress(bankId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: EnterpriseStressRunCreate) =>
      authFetch<EnterpriseStressRead>(`/banks/${bankId}/enterprise-stress/runs`, {
        method: 'POST',
        body: payload,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [RUN_KEY] }),
  });
}

/** Latest immutable run for one (period × scenario). 404 (no run yet) → null. */
export function useLatestEnterpriseStress(
  bankId: string | undefined,
  periodId: string | undefined,
  scenarioId: string | undefined
) {
  return useQuery({
    queryKey: [RUN_KEY, bankId, periodId, scenarioId],
    queryFn: async () => {
      try {
        return await authFetch<EnterpriseStressRead>(
          `/banks/${bankId}/enterprise-stress/latest`,
          { query: { reporting_period_id: periodId, scenario_id: scenarioId } }
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(bankId && periodId && scenarioId),
  });
}

/**
 * The re-openable run registry (docs/stress.md §4 item 5). The backend exposes
 * the latest immutable run per (period × scenario), so the registry is the set
 * of latest runs across the given scenarios for a period — each row re-opens its
 * immutable run, and switching the period diffs the same scenarios across
 * quarters. Runs that have never executed for a scenario are simply absent.
 */
export function useEnterpriseStressRegistry(
  bankId: string | undefined,
  periodId: string | undefined,
  scenarioIds: string[]
) {
  const sorted = [...scenarioIds].sort();
  return useQuery({
    queryKey: [RUN_KEY, 'registry', bankId, periodId, sorted],
    queryFn: async () => {
      const results = await Promise.all(
        sorted.map(async (scenarioId) => {
          try {
            return await authFetch<EnterpriseStressRead>(
              `/banks/${bankId}/enterprise-stress/latest`,
              { query: { reporting_period_id: periodId, scenario_id: scenarioId } }
            );
          } catch (error) {
            if (error instanceof ApiError && error.status === 404) return null;
            throw error;
          }
        })
      );
      return results.filter((run): run is EnterpriseStressRead => run !== null);
    },
    enabled: Boolean(bankId && periodId && sorted.length > 0),
  });
}

/**
 * The full, re-openable run history (docs/stress.md §4.5). Unlike
 * {@link useEnterpriseStressRegistry} (latest-per-scenario), this lists EVERY
 * persisted enterprise-stress run — newest first — as a lightweight summary,
 * optionally scoped to one reporting period. Re-open a row with
 * {@link useReopenEnterpriseStressRun}.
 */
export function useEnterpriseStressRunHistory(
  bankId: string | undefined,
  periodId?: string
) {
  return useQuery({
    queryKey: [RUN_KEY, 'history', bankId, periodId ?? null],
    queryFn: () =>
      authFetch<EnterpriseStressRunSummary[]>(`/banks/${bankId}/enterprise-stress/runs`, {
        query: periodId ? { reporting_period_id: periodId } : undefined,
      }),
    enabled: Boolean(bankId),
  });
}

/** Re-open one immutable run by id → its full projection + Appendix II. */
export function useReopenEnterpriseStressRun(bankId: string | undefined) {
  return useMutation({
    mutationFn: (runId: string) =>
      authFetch<EnterpriseStressRead>(`/banks/${bankId}/enterprise-stress/runs/${runId}`),
  });
}

// --- Enterprise-stress sign-off / Board attestation (docs/stress.md §3.8) -----

const SIGNOFF_KEY = 'stress-signoffs';

/** All sign-offs for a bank (newest first); filter by run_id in the panel. */
export function useBankStressSignoffs(bankId: string | undefined) {
  return useQuery({
    queryKey: [SIGNOFF_KEY, bankId],
    queryFn: () =>
      authFetch<StressSignoffListRead>(`/banks/${bankId}/enterprise-stress/signoffs`),
    select: (data): StressSignoffSummary[] => data.signoffs,
    enabled: Boolean(bankId),
  });
}

/** The full governance record for one sign-off. */
export function useStressSignoff(bankId: string | undefined, signoffId: string | undefined) {
  return useQuery({
    queryKey: [SIGNOFF_KEY, bankId, signoffId],
    queryFn: () =>
      authFetch<StressSignoffRead>(`/banks/${bankId}/enterprise-stress/signoffs/${signoffId}`),
    enabled: Boolean(bankId && signoffId),
  });
}

function useSignoffMutation<TBody>(bankId: string | undefined, path: (id: string) => string, method: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ signoffId, body }: { signoffId: string; body: TBody }) =>
      authFetch<StressSignoffRead>(`/banks/${bankId}${path(signoffId)}`, { method, body }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [SIGNOFF_KEY] }),
  });
}

export function useCreateStressSignoff(bankId: string | undefined) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: StressSignoffCreate) =>
      authFetch<StressSignoffRead>(`/banks/${bankId}/enterprise-stress/signoffs`, {
        method: 'POST',
        body,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: [SIGNOFF_KEY] }),
  });
}

export const useUpdateStressSignoff = (bankId: string | undefined) =>
  useSignoffMutation<Partial<StressSignoffCreate>>(bankId, (id) => `/enterprise-stress/signoffs/${id}`, 'PATCH');
export const useSubmitStressSignoff = (bankId: string | undefined) =>
  useSignoffMutation<{ reason: string }>(bankId, (id) => `/enterprise-stress/signoffs/${id}/submit`, 'POST');
export const useAttestStressSignoff = (bankId: string | undefined) =>
  useSignoffMutation<StressSignoffAttestation>(bankId, (id) => `/enterprise-stress/signoffs/${id}/attest`, 'POST');
export const useWithdrawStressSignoff = (bankId: string | undefined) =>
  useSignoffMutation<{ reason: string }>(bankId, (id) => `/enterprise-stress/signoffs/${id}/withdraw`, 'POST');

export type { AppendixIITables, EnterpriseProjection, EnterpriseStressRead };
