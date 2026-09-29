"use client";

import { useQuery } from "@tanstack/react-query";
import { authorizationApi } from "./client";
import { institutionBranchesKey, parseBranchDirectory } from "./grants";
import { useQueryAuthorityScope } from "./useQueryScope";

export const ORGANIZATION_MEMBERS_QUERY_KEY = [
  "settings",
  "organization-members",
] as const;

export function useGrantAdministrationAccess(): boolean {
  const query = useQuery({
    queryKey: ORGANIZATION_MEMBERS_QUERY_KEY,
    queryFn: () => authorizationApi.listOrganizationMembers(),
    retry: false,
  });

  return query.isSuccess;
}

export const ORGANIZATION_INSTITUTIONS_QUERY_KEY = [
  "settings",
  "organization-institutions",
] as const;

/**
 * Every institution in the organization, for scoping a grant.
 *
 * Account-plane data from `/organization/institutions`. The composer used to
 * read `useBanks()`, which the API filters to the institutions the CALLER can
 * view — empty for an Owner holding Account alone — so an Owner could only
 * write organization-wide grants. Grant administration must not borrow its
 * catalogue from the Owner's own operational coverage.
 */
export function useGrantableInstitutions() {
  return useQuery({
    queryKey: ORGANIZATION_INSTITUTIONS_QUERY_KEY,
    queryFn: () => authorizationApi.listOrganizationInstitutions(),
    retry: false,
  });
}

/**
 * The branches of one institution, for the composer's coverage control.
 *
 * Keyed per organization, per signed-in actor, per authorization generation and
 * per INSTITUTION — `institutionBranchesKey`. That last dimension is the one a
 * settings key has never needed before and the one that matters most here: two
 * institutions of one organization are a single RLS tenant, so a key without it
 * would hand a sibling bank's branch list to the composer and the Owner would
 * grant codes that mean nothing there.
 *
 * `retry: false` on purpose: a failure has to be VISIBLE promptly, because the
 * only honest thing the control can do without this list is decline to offer
 * branch coverage and say why. A silent retry loop would leave it looking
 * merely slow.
 */
export function useInstitutionBranches(institutionId: string | null) {
  const scope = useQueryAuthorityScope();
  return useQuery({
    queryKey: institutionBranchesKey(scope, institutionId),
    queryFn: async () =>
      parseBranchDirectory(
        await authorizationApi.listOrganizationInstitutionBranches({
          institutionId: institutionId!,
        }),
        institutionId!,
      ),
    enabled: Boolean(institutionId),
    retry: false,
  });
}
