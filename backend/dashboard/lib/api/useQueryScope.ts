'use client';

import {
  createContext,
  createElement,
  useContext,
  useMemo,
  type ReactNode,
} from 'react';
import { useSession } from 'next-auth/react';
import { useImpersonation } from '@/components/impersonation/useImpersonation';
import { queryAuthorityScope, type QueryAuthorityScope } from './queryPolicy';

const QueryAuthorityContext = createContext<QueryAuthorityScope | null>(null);

/** Resolve the verified tenant or staff-inspection authority once at the root. */
export function useResolvedQueryAuthorityScope(): QueryAuthorityScope {
  const { data: session } = useSession();
  const inspection = useImpersonation();
  const organizationId = inspection.impersonating
    ? inspection.org
    : session?.user?.organizationId;
  const email = inspection.impersonating
    ? inspection.operator
      ? `operator:${inspection.operator}`
      : 'operator:pending'
    : session?.user?.email;
  const roles = inspection.impersonating ? ['examiner'] : session?.user?.roles;
  const rolesKey = [...(roles ?? [])].sort().join(',');
  return useMemo(
    () =>
      queryAuthorityScope(
        organizationId,
        email,
        rolesKey ? rolesKey.split(',') : []
      ),
    [email, organizationId, rolesKey]
  );
}

export function QueryAuthorityScopeProvider({
  scope,
  children,
}: {
  scope: QueryAuthorityScope;
  children: ReactNode;
}) {
  return createElement(QueryAuthorityContext.Provider, { value: scope }, children);
}

/** Cache scope for query hooks; access-token rotation is deliberately inert. */
export function useQueryAuthorityScope(): QueryAuthorityScope {
  const scope = useContext(QueryAuthorityContext);
  if (!scope) {
    throw new Error(
      'useQueryAuthorityScope must be used within QueryAuthorityScopeProvider.'
    );
  }
  return scope;
}
