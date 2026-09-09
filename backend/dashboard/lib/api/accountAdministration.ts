import type { EffectiveAuthorityRead } from "@aequoros/risk-service-api";
import { hasEffectiveCapability } from "../modules";

export function hasAccountAdministrationAuthority(
  authority: EffectiveAuthorityRead | undefined,
): boolean {
  return hasEffectiveCapability(
    authority?.organizationCapabilities ?? [],
    "account",
    "restricted",
    "administer",
  );
}

export function hasAccountDirectoryAuthority(
  authority: EffectiveAuthorityRead | undefined,
): boolean {
  return hasEffectiveCapability(
    authority?.organizationCapabilities ?? [],
    "account",
    "restricted",
    "view",
  );
}
