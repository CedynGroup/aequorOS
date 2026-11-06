"use client";

import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useImpersonation } from "@/components/impersonation/useImpersonation";
import { hasEffectiveCapability } from "@/lib/modules";
import { wholeInstitutionCapabilities } from "./dataScope";

/** The backend RoleBundle modules with a configure permission (#184). */
export type ConfigureModule = "liq" | "credit" | "data" | "markets" | "reg";

const MODULE_LABELS: Record<ConfigureModule, string> = {
  liq: "Liquidity",
  credit: "Credit",
  data: "Data",
  markets: "Markets",
  reg: "Regulatory Reporting",
};

function useEligibilityGuard(
  profile: ReturnType<typeof useUserProfile>,
  impersonating: boolean,
): string | null {
  if (profile.isLoading) return "Checking configuration access…";
  if (profile.error)
    return "Could not verify configuration access. Reload the page to retry.";
  if (impersonating)
    return "Configuration is not available while inspecting as another principal.";
  return null;
}

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
  const guard = useEligibilityGuard(profile, inspection.impersonating);
  const allowed =
    !guard &&
    Boolean(bankId) &&
    hasEffectiveCapability(
      wholeInstitutionCapabilities(capabilities),
      module,
      "restricted",
      "configure",
    );
  const reason =
    guard ??
    `Requires ${MODULE_LABELS[module]} configure access with Restricted data for this institution.`;
  return { allowed, reason };
}

/**
 * Organization-wide configuration authority: signing policies and placement
 * templates configure how EVERY bank's returns are attested, so the backend
 * demands an organization-scope binding (VAL-CFG-008) and projects it into
 * `organization_capabilities`. One bank's grant deliberately never qualifies,
 * and no scalar role substitutes — the mirror of the bank-scope hook above.
 */
export function useOrganizationConfigureEligibility(module: ConfigureModule) {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const guard = useEligibilityGuard(profile, inspection.impersonating);
  const allowed =
    !guard &&
    hasEffectiveCapability(
      profile.effectiveAuthority?.organizationCapabilities ?? [],
      module,
      "restricted",
      "configure",
    );
  const reason =
    guard ??
    `Requires organization-scoped ${MODULE_LABELS[module]} configure access with Restricted data.`;
  return { allowed, reason };
}
