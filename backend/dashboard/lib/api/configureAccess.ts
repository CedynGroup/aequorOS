"use client";

import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useImpersonation } from "@/components/impersonation/useImpersonation";
import { hasEffectiveCapability } from "@/lib/modules";
import { wholeInstitutionCapabilities } from "./dataScope";

/** The backend RoleBundle modules with a configure permission (#184). */
export type ConfigureModule = "liq" | "credit" | "data" | "markets";

const MODULE_LABELS: Record<ConfigureModule, string> = {
  liq: "Liquidity",
  credit: "Credit",
  data: "Data",
  markets: "Markets",
};

/** Configuration is independent of navigation/read authority and scalar roles. */
export function useConfigureEligibility(
  bankId: string | undefined,
  module: ConfigureModule,
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
    : profile.error
      ? "Could not verify configuration access. Reload the page to retry."
      : `Requires ${MODULE_LABELS[module]} configure access with Restricted data for this institution.`;
  return { allowed, reason };
}
