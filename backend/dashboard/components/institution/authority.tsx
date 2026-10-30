"use client";

import type { ButtonHTMLAttributes } from "react";
import { useBankContext } from "@/components/shell/BankContext";
import { DisabledWithReason } from "@/components/ui/DisabledWithReason";
import { wholeInstitutionCapabilities } from "@/lib/api/dataScope";
import { hasEffectiveCapability } from "@/lib/modules";

type Classification = "confidential" | "restricted";
type Action = "view" | "create" | "edit";

/** Match the selected institution and one complete whole-institution capability. */
export function useInstitutionAuthority() {
  const { institutionCapabilities, authorityPending } = useBankContext();
  const capabilities = wholeInstitutionCapabilities(institutionCapabilities);
  const allows = (sensitivity: Classification, permission: Action) =>
    !authorityPending &&
    hasEffectiveCapability(
      capabilities,
      "institution",
      sensitivity,
      permission,
    );
  return {
    allows,
    generalView: allows("confidential", "view"),
    restrictedView: allows("restricted", "view"),
  };
}

export function institutionActionReason(
  sensitivity: Classification,
  permission: Action,
) {
  const classification =
    sensitivity === "restricted" ? "Restricted" : "Confidential";
  const action = permission.charAt(0).toUpperCase() + permission.slice(1);
  return `Requires Institution · ${classification} · ${action} for the whole institution. Ask your organization owner to grant it.`;
}

/** Native disabled controls retain a keyboard-accessible explanation. */
export function InstitutionAction({
  sensitivity = "confidential",
  permission,
  disabled,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  sensitivity?: Classification;
  permission: Exclude<Action, "view">;
}) {
  const authority = useInstitutionAuthority();
  if (authority.allows(sensitivity, permission)) {
    return <button type="button" {...props} disabled={disabled} />;
  }
  return (
    <DisabledWithReason
      reason={institutionActionReason(sensitivity, permission)}
    >
      {(descriptionId) => (
        <button
          type="button"
          {...props}
          disabled
          aria-describedby={descriptionId}
        />
      )}
    </DisabledWithReason>
  );
}
