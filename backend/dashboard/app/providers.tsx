'use client';

import { useEffect } from 'react';
import { SessionProvider, signOut, useSession } from 'next-auth/react';
import { usePathname } from 'next/navigation';

import { setAccessToken } from '@/lib/api/token';
import { LOGIN_URL } from '@/lib/loginUrl';
import ProfileProvider from '@/components/profile/ProfileProvider';
import ImpersonationBanner from '@/components/impersonation/ImpersonationBanner';
import QueryAuthorityBoundary from '@/lib/api/QueryAuthorityBoundary';
import { queryAuthorityScope } from '@/lib/api/queryPolicy';
import { useResolvedQueryAuthorityScope } from '@/lib/api/useQueryScope';

const PUBLIC_QUERY_SCOPE = queryAuthorityScope('public', 'anonymous', []);

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

function AuthorityQueryBoundary({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const scope = useResolvedQueryAuthorityScope();
  const publicRoute = pathname === '/login' || pathname === '/inspect';
  return (
    <QueryAuthorityBoundary
      scope={publicRoute ? PUBLIC_QUERY_SCOPE : scope}
      fallback={<AuthorityLoading />}
    >
      <ProfileProvider>{children}</ProfileProvider>
    </QueryAuthorityBoundary>
  );
}

function AuthorityLoading() {
  return (
    <div
      className="min-h-screen flex items-center justify-center bg-surface-alt"
      aria-busy="true"
      aria-label="Loading authenticated workspace"
    >
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-action/25 border-t-action" />
    </div>
  );
}

export default function Providers({ children }: { children: React.ReactNode }) {
  return (
    // Re-pull the session periodically (and on window focus) so the rotated
    // access token propagates to the client cache before it expires.
    <SessionProvider refetchInterval={10 * 60} refetchOnWindowFocus>
      <TokenSync />
      {/* App-wide staff-inspection banner. Renders nothing on a normal session
          (no hand-off cookie), so it is inert outside impersonation. */}
      <ImpersonationBanner />
      <AuthorityQueryBoundary>{children}</AuthorityQueryBoundary>
    </SessionProvider>
  );
}
