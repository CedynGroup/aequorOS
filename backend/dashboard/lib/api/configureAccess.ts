"use client";

import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useImpersonation } from "@/components/impersonation/useImpersonation";
import { hasEffectiveCapability } from "@/lib/modules";
import { wholeInstitutionCapabilities } from "./dataScope";

/** Configuration is independent of navigation/read authority and scalar roles. */
export function useConfigureEligibility(
  bankId: string | undefined,
  module: "liq" | "credit",
) {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const capabilities =
    profile.effectiveAuthority?.institutionCapabilities.find(
      (entry) => entry.institutionId === bankId,
    )?.capabilities ?? [];
  const allowed =
    Boolean(bankId) &&
    !profile.isLoading &&
    !profile.error &&
    !inspection.impersonating &&
    hasEffectiveCapability(
      wholeInstitutionCapabilities(capabilities),
      module,
      "restricted",
      "configure",
    );
  const reason = profile.isLoading
    ? "Checking configuration access…"
    : `Requires ${module === "liq" ? "Liquidity" : "Credit"} configure access with Restricted data for this institution.`;
  return { allowed, reason };
}
