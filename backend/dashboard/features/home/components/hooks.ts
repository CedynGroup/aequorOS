"use client";

/**
 * Command Center data hooks.
 *
 * `useEffectivePeriod` fixes the "populated KPIs next to an empty live card"
 * contradiction: the header can select a latest reporting period that holds no
 * facts (e.g. a period created by ingestion but never activated). Every panel
 * on the home page reads the resolved *effective* period instead — the
 * selected period when it has facts, otherwise the newest period that does —
 * and the page labels the fallback explicitly.
 *
 * Only read helpers live here; module data comes from the shared hooks in
 * lib/api/hooks.ts (deduped by TanStack Query key across panels).
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  BankFactsRead,
  BankReportingPeriodRead,
} from "@aequoros/risk-service-api";
import { apiCall, banksApi } from "@/lib/api/client";
import { useBankContext } from "@/components/shell/BankContext";
import { useBankPeriodFacts } from "@/lib/api/hooks";
import { scopedQueryKey } from "@/lib/api/queryPolicy";
import { useQueryAuthorityScope } from "@/lib/api/useQueryScope";

/** How many older periods the fallback probe will inspect before giving up. */
const FALLBACK_PROBE_LIMIT = 12;

/** Whether a period facts payload contains anything the engines can compute from. */
export function hasAnyFacts(facts: BankFactsRead): boolean {
  return (
    facts.balanceSheet.length > 0 ||
    facts.capitalComponents.length > 0 ||
    facts.depositBehavior.length > 0 ||
    facts.lcrInflows.length > 0 ||
    facts.loanExposures.length > 0 ||
    facts.marketRisk.length > 0 ||
    facts.offBalance.length > 0 ||
    facts.operationalIncome.length > 0 ||
    facts.securities.length > 0
  );
}

export type EffectivePeriodResult = {
  /** The period every Command Center panel should read. */
  period: BankReportingPeriodRead | null;
  /** The header-selected period (for the fallback notice copy). */
  selectedPeriod: BankReportingPeriodRead | null;
  /** True when the page fell back from the selected period to an older one. */
  isFallback: boolean;
  /** True while the selected period is still being checked / probed. */
  isResolving: boolean;
  /** True when no reporting period in the bank holds any facts. */
  isEmpty: boolean;
  /** Error from the facts check, if any (surfaced by the page boundary). */
  error: unknown;
  periodsUnavailable: boolean;
};

/**
 * Resolve the effective reporting period for the Command Center.
 *
 * 1. Read the selected period's canonical facts (shared `['facts', …]` key,
 *    so the balance-sheet strip reuses the response).
 * 2. If the selected period is empty, probe older periods newest-first for
 *    the latest one that actually has facts, warming the facts cache for it.
 * Without facts permission, retain the selected period for independent panels
 * and suppress both the facts read and fallback probes.
 */
export function useEffectivePeriod(): EffectivePeriodResult {
  const { bank, period, periods, periodsStatus, periodsError, isLoading } =
    useBankContext();
  const bankId = bank?.id;
  const queryClient = useQueryClient();
  const scope = useQueryAuthorityScope();

  const selectedFacts = useBankPeriodFacts(bankId, period?.id);
  const selectedEmpty =
    selectedFacts.isSuccess && !hasAnyFacts(selectedFacts.data);

  const fallback = useQuery({
    queryKey: scopedQueryKey(
      "home-effective-period",
      scope,
      bankId ?? null,
      period?.id ?? null,
      periods.map((p) => p.id).join(","),
    ),
    enabled:
      selectedFacts.access === "allowed" &&
      Boolean(bankId && period) &&
      selectedEmpty,
    staleTime: 60_000,
    queryFn: async () => {
      // `periods` is sorted period-end descending by BankContext.
      const candidates = periods
        .filter((p) => p.id !== period!.id)
        .slice(0, FALLBACK_PROBE_LIMIT);
      for (const candidate of candidates) {
        const facts = await apiCall(() =>
          banksApi.getBankPeriodFacts({
            bankId: bankId!,
            periodId: candidate.id,
          }),
        );
        if (hasAnyFacts(facts)) {
          // Warm the shared facts cache so downstream panels reuse it.
          queryClient.setQueryData(
            scopedQueryKey("facts", scope, bankId ?? null, candidate.id),
            facts,
          );
          return candidate.id;
        }
      }
      return null;
    },
  });

  const base = {
    period,
    selectedPeriod: period,
    isFallback: false,
    isResolving: false,
    isEmpty: false,
    error: null,
    periodsUnavailable: false,
  } satisfies EffectivePeriodResult;

  if (isLoading || periodsStatus === "loading")
    return { ...base, isResolving: true };
  if (periodsStatus === "denied") return { ...base, periodsUnavailable: true };
  if (periodsStatus === "error") return { ...base, error: periodsError };
  if (!period) return { ...base, isEmpty: true };
  if (selectedFacts.access === "loading") return { ...base, isResolving: true };
  if (selectedFacts.access === "denied") return base;
  if (selectedFacts.isError) return { ...base, error: selectedFacts.error };
  if (selectedFacts.isPending) return { ...base, isResolving: true };
  if (!selectedEmpty) return base;
  if (fallback.isPending) return { ...base, isResolving: true };
  if (fallback.isError) return { ...base, error: fallback.error };

  const fallbackPeriod = fallback.data
    ? (periods.find((p) => p.id === fallback.data) ?? null)
    : null;
  return {
    ...base,
    period: fallbackPeriod,
    isFallback: Boolean(fallbackPeriod),
    isEmpty: !fallbackPeriod,
  };
}
