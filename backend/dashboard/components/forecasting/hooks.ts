'use client';

/**
 * Composed forecasting hooks — presentation-layer combinations of the
 * generated-client hooks in lib/api/hooks.ts. No new endpoints: everything
 * here fans out over the existing forecast-run resources.
 */

import type { ForecastRunRead } from '@aequoros/risk-service-api';
import { useForecastRun, useForecastRuns } from '@/lib/api/hooks';
import { latestSucceededId } from './lib';

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
 */
export function useScenarioRunSet(bankId: string | undefined): ScenarioRunSet {
  const runsQuery = useForecastRuns(bankId, { limit: 50 });
  const runs = runsQuery.data?.runs ?? [];

  const baseId = latestSucceededId(runs, 'base');
  const adverseId = latestSucceededId(runs, 'adverse');
  const severeId = latestSucceededId(runs, 'severely_adverse');

  const baseQuery = useForecastRun(bankId, baseId);
  const adverseQuery = useForecastRun(bankId, adverseId);
  const severeQuery = useForecastRun(bankId, severeId);

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
