"use client";

import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { useUserProfile } from "../../components/profile/ProfileProvider";
import { useImpersonation } from "../../components/impersonation/useImpersonation";
import { hasDataCapability } from "../modules";

/** Exact projected eligibility. The server rechecks the binding and object state. */
export function useDataPermission(
  bankId: string | undefined,
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
): boolean {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const capabilities =
    profile.effectiveAuthority?.institutionCapabilities.find(
      (entry) => entry.institutionId === bankId,
    )?.capabilities ?? [];
  return (
    Boolean(bankId) &&
    !profile.isLoading &&
    (permission === "view" || !inspection.impersonating) &&
    hasDataCapability(capabilities, sensitivity, permission)
  );
}
