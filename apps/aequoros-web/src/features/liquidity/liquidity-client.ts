import {
  Configuration,
  LiquidityApi,
  type LiquidityFindingRead,
  type LiquidityFindingReview,
  type LiquiditySummaryRead,
} from "@aequoros/risk-service-api";

import type { TenantHeaders } from "../../lib/api";
import { apiBaseUrl } from "../../lib/constants";

export interface LiquidityReviewClient {
  summary(
    tenant: TenantHeaders,
    caseId: string,
    scenarioId?: string,
    runId?: string,
  ): Promise<LiquiditySummaryRead>;
  review(
    tenant: TenantHeaders,
    caseId: string,
    findingId: string,
    payload: LiquidityFindingReview,
  ): Promise<LiquidityFindingRead>;
}

function generatedLiquidityApi() {
  const basePath = apiBaseUrl().replace(/\/api\/v1\/?$/, "");
  return new LiquidityApi(new Configuration({ basePath }));
}

export const liquidityReviewClient: LiquidityReviewClient = {
  summary(tenant, caseId, scenarioId, runId) {
    return generatedLiquidityApi().getLiquiditySummary({
      caseId,
      xOrgId: tenant.orgId,
      xUserId: tenant.userId,
      scenarioId,
      runId,
    });
  },
  review(tenant, caseId, findingId, payload) {
    return generatedLiquidityApi().reviewLiquidityFinding({
      caseId,
      findingId,
      xOrgId: tenant.orgId,
      xUserId: tenant.userId,
      liquidityFindingReview: payload,
    });
  },
};
