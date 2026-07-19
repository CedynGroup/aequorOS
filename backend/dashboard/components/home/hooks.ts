'use client';

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

import { useQuery, useQueryClient } from '@tanstack/react-query';
import type {
  BankFactsRead,
  BankReportingPeriodRead,
} from '@aequoros/risk-service-api';
import { apiCall, banksApi } from '@/lib/api/client';
import { useBankContext } from '@/components/shell/BankContext';
import { useBankPeriodFacts } from '@/lib/api/hooks';


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
};

/**
 * Resolve the effective reporting period for the Command Center.
 *
 * 1. Read the selected period's canonical facts (shared `['facts', …]` key,
 *    so the balance-sheet strip reuses the response).
 * 2. If the selected period is empty, probe older periods newest-first for
 *    the latest one that actually has facts, warming the facts cache for it.
 */
export function useEffectivePeriod(): EffectivePeriodResult {
  const { bank, period, periods } = useBankContext();
  const bankId = bank?.id;
  const queryClient = useQueryClient();

  const selectedFacts = useBankPeriodFacts(bankId, period?.id);
  // undefined → still loading; true/false → known.
  const selectedEmpty: boolean | undefined = selectedFacts.isError
    ? true
    : selectedFacts.data
      ? !hasAnyFacts(selectedFacts.data)
      : undefined;

  const fallback = useQuery({
    queryKey: [
      'home-effective-period',
      bankId,
      period?.id,
      periods.map((p) => p.id).join(','),
    ],
    enabled: Boolean(bankId && period) && selectedEmpty === true,
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
          })
        );
        if (hasAnyFacts(facts)) {
          // Warm the shared facts cache so downstream panels reuse it.
          queryClient.setQueryData(['facts', bankId, candidate.id], facts);
          return candidate.id;
        }
      }
      return null;
    },
  });

  if (!bank || !period) {
    return {
      period: null,
      selectedPeriod: null,
      isFallback: false,
      isResolving: true,
      isEmpty: false,
      error: null,
    };
  }

  if (selectedEmpty === undefined) {
    return {
      period: null,
      selectedPeriod: period,
      isFallback: false,
      isResolving: true,
      isEmpty: false,
      error: null,
    };
  }

  if (selectedEmpty === false) {
    return {
      period,
      selectedPeriod: period,
      isFallback: false,
      isResolving: false,
      isEmpty: false,
      error: null,
    };
  }

  // Selected period is empty — fall back to the newest computed period.
  if (fallback.isLoading) {
    return {
      period: null,
      selectedPeriod: period,
      isFallback: false,
      isResolving: true,
      isEmpty: false,
      error: null,
    };
  }

  const fallbackPeriod = fallback.data
    ? (periods.find((p) => p.id === fallback.data) ?? null)
    : null;

  return {
    period: fallbackPeriod,
    selectedPeriod: period,
    isFallback: Boolean(fallbackPeriod),
    isResolving: false,
    isEmpty: !fallbackPeriod,
    error: fallback.error ?? null,
  };
}
