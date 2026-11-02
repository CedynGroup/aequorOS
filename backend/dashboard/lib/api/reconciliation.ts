"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ReconciliationApi,
  type ReconciliationExceptionCreate,
} from "@aequoros/risk-service-api";
import { apiCall, configuration } from "./client";
import { useDataPermission } from "./dataAccess";
import { scopedQueryKey } from "./queryPolicy";
import { useQueryAuthorityScope } from "./useQueryScope";

const api = new ReconciliationApi(configuration);

export function useReconciliationExceptions(bankId: string | undefined) {
  const scope = useQueryAuthorityScope();
  const allowed = useDataPermission(bankId, "view", "confidential");
  return useQuery({
    queryKey: scopedQueryKey("de-reconciliation", scope, bankId ?? null),
    queryFn: () =>
      apiCall(() => api.listReconciliationExceptions({ bankId: bankId! })),
    enabled: allowed,
  });
}

type Decision =
  | { action: "propose"; payload: ReconciliationExceptionCreate }
  | { action: "approve"; exceptionId: string; label: string }
  | { action: "revoke"; exceptionId: string; label: string; reason: string };

export function useReconciliationDecision(bankId: string | undefined) {
  const client = useQueryClient();
  const scope = useQueryAuthorityScope();
  return useMutation({
    mutationFn: (decision: Decision) =>
      apiCall(() => {
        if (decision.action === "propose")
          return api.proposeReconciliationException({
            bankId: bankId!,
            reconciliationExceptionCreate: decision.payload,
          });
        if (decision.action === "approve")
          return api.approveReconciliationException({
            bankId: bankId!,
            exceptionId: decision.exceptionId,
            reconciliationExceptionApprove: { approvedBy: decision.label },
          });
        return api.revokeReconciliationException({
          bankId: bankId!,
          exceptionId: decision.exceptionId,
          reconciliationExceptionRevoke: {
            revokedBy: decision.label,
            reason: decision.reason,
          },
        });
      }),
    onSuccess: () =>
      client.invalidateQueries({
        queryKey: scopedQueryKey("de-reconciliation", scope, bankId ?? null),
      }),
  });
}
