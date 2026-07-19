'use client';

import { useEffect, useState } from 'react';
import { SessionProvider, useSession } from 'next-auth/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { setAccessToken } from '@/lib/api/token';

/** Keeps the API client's bearer token in sync with the NextAuth session. */
function TokenSync() {
  const { data: session } = useSession();
  useEffect(() => {
    setAccessToken(session?.accessToken ?? null);
  }, [session?.accessToken]);
  return null;
}

export default function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            retry: 1,
            refetchOnWindowFocus: false,
          },
        },
      })
  );

  return (
    <SessionProvider>
      <TokenSync />
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </SessionProvider>
  );
}
