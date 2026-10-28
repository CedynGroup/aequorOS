"use client";

import { useEffect, useState, type ReactNode } from "react";
import {
  QueryCache,
  QueryClient,
  QueryClientProvider,
} from "@tanstack/react-query";
import { QueryAuthorityScopeProvider } from "./useQueryScope";
import {
  bindProtectedDataCache,
  clearProtectedData,
  isProtectedDataError,
} from "./protectedData";
import type { QueryAuthorityScope } from "./queryPolicy";

export function createAuthorityQueryClient(): QueryClient {
  return new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => {
        if (isProtectedDataError(error)) query.setState({ data: undefined });
      },
    }),
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

  useEffect(() => {
    const unsubscribe = bindProtectedDataCache(queryClient);
    return () => {
      unsubscribe();
      queryClient.clear();
      clearProtectedData();
    };
  }, [queryClient]);

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
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
