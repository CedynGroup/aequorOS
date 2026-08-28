'use client';

import {
  createContext,
  createElement,
  useEffect,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { useSession } from 'next-auth/react';
import { useImpersonation } from '../../components/impersonation/useImpersonation';
import { resolveImpersonationStatus } from './impersonation';
import { queryAuthorityScope, type QueryAuthorityScope } from './queryPolicy';

const QueryAuthorityContext = createContext<QueryAuthorityScope | null>(null);

/** Resolve the verified tenant or staff-inspection authority once at the root. */
export function useResolvedQueryAuthorityScope(): QueryAuthorityScope | null {
  const { data: session, status } = useSession();
  const inspection = useImpersonation();
  const [inspectionResolved, setInspectionResolved] = useState(false);

  useEffect(() => {
    let active = true;
    let retry: ReturnType<typeof setTimeout> | undefined;
    const resolve = async () => {
      const result = await resolveImpersonationStatus();
      if (!active) return;
      if (result.confirmed) {
        setInspectionResolved(true);
        return;
      }
      retry = setTimeout(resolve, 1_000);
    };
    void resolve();
    return () => {
      active = false;
      if (retry) clearTimeout(retry);
    };
  }, []);

  const organizationId = inspection.impersonating
    ? inspection.org
    : session?.user?.organizationId;
  const email = inspection.impersonating
    ? inspection.operator
      ? `operator:${inspection.operator}`
      : null
    : session?.user?.email;
  const roles = inspection.impersonating ? ['examiner'] : session?.user?.roles;
  const rolesKey = [...(roles ?? [])].sort().join(',');
  const verified = inspection.impersonating
    ? !inspection.expired && Boolean(inspection.token && organizationId && email)
    : status === 'authenticated' &&
      Boolean(session?.accessToken && !session.error && organizationId && email);
  return useMemo(
    () =>
      inspectionResolved && verified
        ? queryAuthorityScope(
            organizationId,
            email,
            rolesKey ? rolesKey.split(',') : [],
          )
        : null,
    [email, inspectionResolved, organizationId, rolesKey, verified],
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
