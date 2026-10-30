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

/** Members and grant administration require an evaluated Organization Owner. */
export function hasGrantAdministrationAuthority(
  authority: EffectiveAuthorityRead | undefined,
): boolean {
  return authority?.canAdministerGrants === true;
}

/** Owners land on Members; everyone else starts with their own access. */
export function accessIndexDestination(
  authority: EffectiveAuthorityRead | undefined,
): "/access/members" | "/access/my-access" {
  return hasGrantAdministrationAuthority(authority)
    ? "/access/members"
    : "/access/my-access";
}
