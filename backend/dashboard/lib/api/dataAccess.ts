"use client";

import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { useUserProfile } from "../../components/profile/ProfileProvider";
import { useImpersonation } from "../../components/impersonation/useImpersonation";
import { hasDataCapability } from "../modules";

export type DataPermissionState = "loading" | "denied" | "allowed";

function usePermissionState(
  bankId: string | undefined,
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
  examinerRead: boolean,
): DataPermissionState {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const capabilities =
    profile.effectiveAuthority?.institutionCapabilities.find(
      (entry) => entry.institutionId === bankId,
    )?.capabilities ?? [];
  if (profile.isLoading) return "loading";
  return Boolean(bankId) &&
    (!inspection.impersonating ||
      (examinerRead &&
        !inspection.expired &&
        Boolean(inspection.token) &&
        Boolean(inspection.operator))) &&
    hasDataCapability(capabilities, sensitivity, permission)
    ? "allowed"
    : "denied";
}

export function useDataPermissionState(
  bankId: string | undefined,
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
): DataPermissionState {
  return usePermissionState(bankId, permission, sensitivity, false);
}

export function useReportingDataPermissionState(
  bankId: string | undefined,
  sensitivity: "confidential" | "restricted",
): DataPermissionState {
  return usePermissionState(bankId, "view", sensitivity, true);
}

export function useDataPermission(
  bankId: string | undefined,
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"],
): boolean {
  return useDataPermissionState(bankId, permission, sensitivity) === "allowed";
}
