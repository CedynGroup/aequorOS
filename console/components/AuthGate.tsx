'use client';

import { useEffect, useState, type ReactNode } from 'react';
import { useRouter } from 'next/navigation';
import { getToken, getWorkforceSession } from '@/lib/api';

/**
 * Client-side auth gate for both session models:
 * - dev token in sessionStorage (local dev), or
 * - workforce OIDC session (HttpOnly cookie, checked via /api/auth/session).
 * Neither present → redirect to /login. Renders nothing until the check runs
 * so protected content never flashes. This is UX-gating only — the operator
 * API independently rejects unauthenticated calls either way.
 */
export default function AuthGate({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    if (getToken()) {
      setReady(true);
      return;
    }
    getWorkforceSession()
      .then((session) => {
        if (!alive) return;
        if (session.authenticated) setReady(true);
        else router.replace('/login');
      })
      .catch(() => alive && router.replace('/login'));
    return () => {
      alive = false;
    };
  }, [router]);

  if (!ready) return null;
  return <>{children}</>;
}
