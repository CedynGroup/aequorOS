"use client";

/**
 * Composed forecasting hooks — presentation-layer combinations of the
 * generated-client hooks in lib/api/hooks.ts. No new endpoints: everything
 * here fans out over the existing forecast-run resources.
 */

import type { ForecastRunRead } from "@aequoros/risk-service-api";
import { useForecastRun, useForecastRuns } from "@/lib/api/hooks";
import { latestSucceededId } from "./lib";

export type ScenarioRunSet = {
  /** Latest succeeded run per preset scenario (undefined while loading / absent). */
  base: ForecastRunRead | undefined;
  adverse: ForecastRunRead | undefined;
  severelyAdverse: ForecastRunRead | undefined;
  isLoading: boolean;
  error: unknown;
  refetch: () => void;
};

/**
 * Latest succeeded forecast run for each preset scenario — powers the
 * base-vs-adverse projection band and the NII scenario sensitivity table.
 *
 * The summaries ride Forecasting aggregated view; each full run is
 * confidential, so `canViewRuns` (the caller's projected authority) decides
 * whether the detail queries are issued at all.
 */
export function useScenarioRunSet(
  bankId: string | undefined,
  canViewRuns: boolean,
): ScenarioRunSet {
  const runsQuery = useForecastRuns(bankId, { limit: 50 });
  const runs = runsQuery.data?.runs ?? [];
  const detailBankId = canViewRuns ? bankId : undefined;

  const baseId = latestSucceededId(runs, "base");
  const adverseId = latestSucceededId(runs, "adverse");
  const severeId = latestSucceededId(runs, "severely_adverse");

  const baseQuery = useForecastRun(detailBankId, baseId);
  const adverseQuery = useForecastRun(detailBankId, adverseId);
  const severeQuery = useForecastRun(detailBankId, severeId);

  return {
    base: baseQuery.data,
    adverse: adverseQuery.data,
    severelyAdverse: severeQuery.data,
    isLoading:
      runsQuery.isLoading ||
      baseQuery.isLoading ||
      adverseQuery.isLoading ||
      severeQuery.isLoading,
    error: runsQuery.error,
    refetch: () => void runsQuery.refetch(),
  };
}
