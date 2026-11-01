"use client";

import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { useUserProfile } from "../../components/profile/ProfileProvider";
import { useImpersonation } from "../../components/impersonation/useImpersonation";
import { hasDataCapability } from "../modules";

export type DataPermissionState = "loading" | "denied" | "allowed";

/** Exact projected eligibility. The server rechecks the binding and object state. */
export function useDataPermissionState(
  bankId: string | undefined,
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
): DataPermissionState {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const capabilities =
    profile.effectiveAuthority?.institutionCapabilities.find(
      (entry) => entry.institutionId === bankId,
    )?.capabilities ?? [];
  if (profile.isLoading) return "loading";
  return Boolean(bankId) &&
    (permission === "view" || !inspection.impersonating) &&
    hasDataCapability(capabilities, sensitivity, permission)
    ? "allowed"
    : "denied";
}

export function useDataPermission(
  bankId: string | undefined,
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
): boolean {
  return useDataPermissionState(bankId, permission, sensitivity) === "allowed";
}
