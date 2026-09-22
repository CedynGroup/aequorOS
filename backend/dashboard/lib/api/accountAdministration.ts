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

/**
 * Where `/access` lands: administrators on the Members table they came to
 * work in, everyone else on their own access summary (docs/rbac.md §8.3).
 */
export function accessIndexDestination(
  authority: EffectiveAuthorityRead | undefined,
): "/access/members" | "/access/my-access" {
  return hasAccountAdministrationAuthority(authority)
    ? "/access/members"
    : "/access/my-access";
}
