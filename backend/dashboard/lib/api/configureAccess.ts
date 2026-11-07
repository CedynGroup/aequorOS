"use client";

import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useImpersonation } from "@/components/impersonation/useImpersonation";
import { hasEffectiveCapability, hasStructuralCapability } from "@/lib/modules";
import { wholeInstitutionCapabilities } from "./dataScope";

/** The backend RoleBundle modules with a configure permission (#184). */
export type ConfigureModule =
  "liq" | "credit" | "data" | "markets" | "reg" | "beh";

const MODULE_LABELS: Record<ConfigureModule, string> = {
  liq: "Liquidity",
  credit: "Credit",
  data: "Data",
  markets: "Markets",
  reg: "Regulatory Reporting",
  beh: "Behavioral Models",
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
 * The BEH proposal queue contains restricted estimate evidence and provenance.
 * Viewing that queue and offering its contextual approval control are separate
 * exact-bank capabilities; neither follows from the aggregated model read or
 * Configurator's propose grant. The selected proposal's maker-checker actor
 * comparison is applied by the queue because it needs the persisted proposer.
 */
export function useBehavioralProposalAccess(bankId: string | undefined) {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const capabilities =
    profile.effectiveAuthority?.institutionCapabilities.find(
      (entry) => entry.institutionId === bankId,
    )?.capabilities ?? [];
  const wholeInstitution = wholeInstitutionCapabilities(capabilities);
  const guard = useEligibilityGuard(profile, inspection.impersonating);
  const canView =
    !guard &&
    Boolean(bankId) &&
    hasEffectiveCapability(wholeInstitution, "beh", "restricted", "view");
  const canApprove =
    !guard &&
    Boolean(bankId) &&
    hasStructuralCapability(wholeInstitution, "beh", "restricted", "approve");
  const viewReason =
    guard ??
    "Behavioral Models Restricted data view access is required to load proposal evidence for this institution.";
  const approveReason =
    guard ??
    (!canView
      ? viewReason
      : "Requires Behavioral Models approve access with Restricted data for this institution.");
  return {
    canView,
    viewReason,
    canReview: canView && canApprove,
    reviewReason: approveReason,
  };
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
