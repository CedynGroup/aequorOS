'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { QueryAuthorityScopeProvider } from './useQueryScope';
import type { QueryAuthorityScope } from './queryPolicy';

function createAuthorityQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        retry: 1,
        refetchOnWindowFocus: false,
      },
    },
  });
}

function AuthorityScopedCache({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createAuthorityQueryClient);

  useEffect(() => () => queryClient.clear(), [queryClient]);

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

export default function QueryAuthorityBoundary({
  scope,
  fallback = null,
  children,
}: {
  scope: QueryAuthorityScope | null;
  fallback?: ReactNode;
  children: ReactNode;
}) {
  if (!scope) return fallback;

  const authorityKey = `${scope.tenantId}|${scope.authorityId}`;
  return (
    <QueryAuthorityScopeProvider scope={scope}>
      <AuthorityScopedCache key={authorityKey}>{children}</AuthorityScopedCache>
    </QueryAuthorityScopeProvider>
  );
}
