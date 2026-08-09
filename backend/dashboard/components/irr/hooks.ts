'use client';

/**
 * IRR-workspace query hooks. Kept module-local (not in lib/api/hooks.ts) —
 * built directly on the generated client wiring in lib/api/client.
 */

import { useQuery } from '@tanstack/react-query';
import { apiCall, regulatoryLiquidityApi } from '@/lib/api/client';


/**
 * Stored IRR results for a bank, newest first.
 * Optionally scoped to one reporting period. Each run stores the full
 * banking-book analysis reproducible from the canonical snapshot.
 */
export function useIrrRuns(
  bankId: string | undefined,
  options: { reportingPeriodId?: string; limit?: number } = {}
) {
  const { reportingPeriodId, limit = 100 } = options;
  return useQuery({
    queryKey: ['reg-runs', bankId, 'irr', reportingPeriodId ?? null, limit, 0],
    queryFn: () =>
      apiCall(() =>
        regulatoryLiquidityApi.listRegulatoryRuns({
          bankId: bankId!,
          module: 'irr',
          reportingPeriodId,
          limit,
        })
      ),
    enabled: Boolean(bankId),
  });
}
