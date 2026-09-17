"use client";

import { useQuery } from "@tanstack/react-query";
import { authorizationApi } from "./client";

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
