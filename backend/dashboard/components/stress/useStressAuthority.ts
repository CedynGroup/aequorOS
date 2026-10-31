"use client";

import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useImpersonation } from "@/components/impersonation/useImpersonation";
import { hasEffectiveCapability, hasStructuralCapability } from "@/lib/modules";

export function stressPermissionReason(permission: string) {
  return `Requires Risk · Confidential · ${permission}. Ask your organization owner or admin for access.`;
}

export function useStressAuthority() {
  const { effectiveAuthority, isLoading } = useUserProfile();
  const inspection = useImpersonation();
  const can = (
    permission: EffectiveCapabilityRead["permission"],
    bankId?: string | null,
    sensitivity: EffectiveCapabilityRead["sensitivity"] = "confidential",
  ) => {
    if (isLoading || (permission !== "view" && inspection.impersonating))
      return false;
    const capabilities = bankId
      ? (effectiveAuthority?.institutionCapabilities.find(
          (institution) => institution.institutionId === bankId,
        )?.capabilities ?? [])
      : (effectiveAuthority?.organizationCapabilities ?? []);
    return permission === "view"
      ? hasEffectiveCapability(capabilities, "risk", sensitivity, permission)
      : hasStructuralCapability(capabilities, "risk", sensitivity, permission);
  };
  const canReadAny =
    can("view") ||
    (effectiveAuthority?.institutionCapabilities ?? []).some((institution) =>
      can("view", institution.institutionId),
    );
  return { can, canReadAny, isLoading };
}
