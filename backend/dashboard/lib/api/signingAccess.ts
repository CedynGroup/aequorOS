"use client";

import type {
  EffectiveAuthorityRead,
  EffectiveCapabilityRead,
  SignatureRecipientRead,
  SigningRole,
} from "@aequoros/risk-service-api";
import { useUserProfile } from "@/components/profile/ProfileProvider";
import { useImpersonation } from "@/components/impersonation/useImpersonation";
import { hasEffectiveCapability } from "@/lib/modules";
import { wholeInstitutionCapabilities } from "./dataScope";

export type SigningModule = Extract<
  EffectiveCapabilityRead["module"],
  "reg" | "risk"
>;

type CapabilityModule = EffectiveCapabilityRead["module"];
type CapabilitySensitivity = EffectiveCapabilityRead["sensitivity"];
type CapabilityPermission = EffectiveCapabilityRead["permission"];

const MODULE_LABELS: Record<SigningModule, string> = {
  reg: "Regulatory Reporting",
  risk: "Risk",
};

const CHECKER_ROLES: readonly SigningRole[] = ["approver", "board"];

function capabilitiesFor(
  authority: EffectiveAuthorityRead | undefined,
  bankId: string | undefined,
): readonly EffectiveCapabilityRead[] {
  return wholeInstitutionCapabilities(
    authority?.institutionCapabilities.find(
      (entry) => entry.institutionId === bankId,
    )?.capabilities ?? [],
  );
}

/** Organization-scope projection never qualifies for an exact-bank ceremony. */
function hasExactInstitutionCapability(
  capabilities: readonly EffectiveCapabilityRead[],
  module: CapabilityModule,
  sensitivity: CapabilitySensitivity,
  permission: CapabilityPermission,
): boolean {
  return capabilities.some(
    (capability) =>
      capability.exactInstitution === true &&
      capability.module === module &&
      capability.sensitivity === sensitivity &&
      capability.permission === permission,
  );
}

/**
 * Structural SIGN_OFF eligibility only. The server still decides maker-checker,
 * step-up, workflow and digest preconditions with the actual object in hand.
 */
export function useSignoffEligibility(
  bankId: string | undefined,
  module: SigningModule,
) {
  const profile = useUserProfile();
  const inspection = useImpersonation();
  const capabilities = capabilitiesFor(profile.effectiveAuthority, bankId);
  const guard = profile.isLoading
    ? "Checking signing access…"
    : profile.error
      ? "Could not verify signing access. Reload the page to retry."
      : inspection.impersonating
        ? "Signing is not available while inspecting as another principal."
        : null;
  const allowed =
    !guard &&
    Boolean(bankId) &&
    hasExactInstitutionCapability(
      capabilities,
      module,
      "restricted",
      "sign_off",
    );
  const reason =
    guard ??
    `Requires ${MODULE_LABELS[module]} sign-off access with Restricted data, exact-institution scope and whole-institution coverage for this institution.`;
  return { allowed, reason };
}

/** Source READ is independent from the REG Signer grant, including for ICAAP. */
export function useSigningSourceEligibility(
  bankId: string | undefined,
  returnFamily: string,
) {
  const profile = useUserProfile();
  const source =
    returnFamily === "icaap"
      ? {
          module: "cap" as const,
          sensitivity: "confidential" as const,
          label: "Capital",
          sensitivityLabel: "Confidential",
        }
      : {
          module: "reg" as const,
          sensitivity: "restricted" as const,
          label: "Regulatory Reporting",
          sensitivityLabel: "Restricted",
        };
  const allowed = hasEffectiveCapability(
    capabilitiesFor(profile.effectiveAuthority, bankId),
    source.module,
    source.sensitivity,
    "view",
  );
  return {
    allowed,
    reason: `Requires ${source.label} view access with ${source.sensitivityLabel} data and whole-institution coverage to read the source return.`,
  };
}

/** REG certification needs source READ, a pending named route, and checker APPROVE. */
export function usePackageSigningEligibility(
  bankId: string | undefined,
  signingRole: SigningRole,
  recipients: readonly SignatureRecipientRead[],
  returnFamily = "regulatory",
) {
  const profile = useUserProfile();
  const signing = useSignoffEligibility(bankId, "reg");
  const source = useSigningSourceEligibility(bankId, returnFamily);
  const capabilities = capabilitiesFor(profile.effectiveAuthority, bankId);
  const canApprove =
    !CHECKER_ROLES.includes(signingRole) ||
    hasExactInstitutionCapability(
      capabilities,
      "reg",
      "restricted",
      "approve",
    );
  const userId = profile.profile?.userId;
  const routed = Boolean(
    userId &&
      recipients.some(
        (recipient) =>
          recipient.status === "pending" &&
          recipient.signingRole === signingRole &&
          recipient.recipientUserId === userId,
      ),
  );
  return {
    allowed: signing.allowed && source.allowed && canApprove && routed,
    reason: !signing.allowed
      ? signing.reason
      : !source.allowed
        ? source.reason
        : !canApprove
          ? "Requires Regulatory Reporting approve access with Restricted data and exact-institution scope. Sign-off authority alone is not checker approval."
          : userId &&
              recipients.some(
                (recipient) =>
                  recipient.status === "pending" &&
                  recipient.signingRole === signingRole,
              )
            ? "You are not the named recipient for this signature. Ask an authorized approver to re-assign the current-cycle route."
            : "No pending current-cycle recipient names you for this signature. Route the signature before preview or step-up.",
  };
}

/** Stress signing adds source READ and the recipient frozen at submission. */
export function useStressSigningEligibility(
  bankId: string | undefined,
  recipientUserId: string | null | undefined,
) {
  const profile = useUserProfile();
  const signing = useSignoffEligibility(bankId, "risk");
  const canReadSource = hasEffectiveCapability(
    capabilitiesFor(profile.effectiveAuthority, bankId),
    "risk",
    "confidential",
    "view",
  );
  const routed = Boolean(
    recipientUserId && profile.profile?.userId === recipientUserId,
  );
  const reason = !signing.allowed
    ? signing.reason
    : !canReadSource
      ? "Requires Risk view access with Confidential data and whole-institution coverage to read the source sign-off."
      : !recipientUserId
        ? "This sign-off has no named recipient. Submit a new governed sign-off with a named Signer before attesting."
        : !routed
          ? "You are not the named recipient for this sign-off. The frozen route cannot be signed around."
          : "";
  return {
    allowed: signing.allowed && canReadSource && routed,
    reason,
  };
}
