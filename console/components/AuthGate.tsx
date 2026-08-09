'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { getToken } from '@/lib/api';

/**
 * Client-side auth gate for the dev-token session model: no token in
 * sessionStorage → redirect to /login. Renders nothing until the check runs
 * (sessionStorage is browser-only) so protected content never flashes.
 *
 * NOTE: OIDC workforce login replaces this gate + the /login page later; the
 * token path stays for local dev.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (getToken()) {
      setReady(true);
    } else {
      router.replace('/login');
    }
  }, [router]);

  if (!ready) return null;
  return <>{children}</>;
}
