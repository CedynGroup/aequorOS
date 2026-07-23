'use client';

import { useEffect, useState } from 'react';
import { SessionProvider, signOut, useSession } from 'next-auth/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { setAccessToken } from '@/lib/api/token';
import { LOGIN_URL } from '@/lib/loginUrl';
import ProfileProvider from '@/components/profile/ProfileProvider';

/** Keeps the API client's bearer token in sync with the NextAuth session. */
function TokenSync() {
  const { data: session } = useSession();
  useEffect(() => {
    // A failed silent refresh means the session can no longer authenticate; send
    // the user back to sign in rather than looping on 401s with a dead token.
    if (session?.error === 'RefreshTokenError') {
      setAccessToken(null);
      void signOut({ redirectTo: LOGIN_URL });
      return;
    }
    setAccessToken(session?.accessToken ?? null);
  }, [session?.accessToken, session?.error]);
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
    // Re-pull the session periodically (and on window focus) so the rotated
    // access token propagates to the client cache before it expires.
    <SessionProvider refetchInterval={10 * 60} refetchOnWindowFocus>
      <TokenSync />
      <QueryClientProvider client={queryClient}>
        <ProfileProvider>{children}</ProfileProvider>
      </QueryClientProvider>
    </SessionProvider>
  );
}
