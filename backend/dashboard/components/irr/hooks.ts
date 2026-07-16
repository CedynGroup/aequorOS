'use client';

/**
 * IRR-workspace query hooks. Kept module-local (not in lib/api/hooks.ts) —
 * built directly on the generated client wiring in lib/api/client.
 */

import { useQuery } from '@tanstack/react-query';
import { apiCall, regulatoryLiquidityApi, tenant } from '@/lib/api/client';

const t = { xOrgId: tenant.orgId, xUserId: tenant.userId } as const;

/**
 * Official (immutable) IRR regulatory runs for a bank, newest first.
 * Optionally scoped to one reporting period. Each run stores the full
 * banking-book analysis with a value-based input hash for reproducibility.
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
          ...t,
          bankId: bankId!,
          module: 'irr',
          reportingPeriodId,
          limit,
        })
      ),
    enabled: Boolean(bankId),
  });
}
