'use client';

import { useQuery } from '@tanstack/react-query';
import { authorizationApi } from './client';

export const ORGANIZATION_MEMBERS_QUERY_KEY = ['settings', 'organization-members'] as const;

export function useGrantAdministrationAccess(): boolean {
  const query = useQuery({
    queryKey: ORGANIZATION_MEMBERS_QUERY_KEY,
    queryFn: () => authorizationApi.listOrganizationMembers(),
    retry: false,
  });

  return query.isSuccess;
}
