'use client';

/**
 * Landing point for an operator inspection hand-off.
 *
 * The console opens `‹dashboard-origin›/inspect#‹urlencoded act_token›`. The
 * token is in the URL fragment (never sent to a server or logged); this client
 * page reads it, POSTs it to /api/impersonation/accept so it is stored in an
 * HttpOnly cookie, clears the fragment from history, and lands on the dashboard.
 *
 * This route is outside the session gate (see middleware): the operator has no
 * tenant session yet, only the hand-off in the fragment.
 */

import { useEffect, useState } from 'react';

type Phase = 'starting' | 'error';

export default function InspectPage() {
  const [phase, setPhase] = useState<Phase>('starting');

  useEffect(() => {
    let cancelled = false;

    async function run() {
      const raw = window.location.hash.startsWith('#')
        ? window.location.hash.slice(1)
        : '';
      const token = raw ? decodeURIComponent(raw) : '';
      if (!token) {
        if (!cancelled) setPhase('error');
        return;
      }

      // Drop the token from the URL/history immediately, regardless of outcome.
      try {
        window.history.replaceState(null, '', window.location.pathname);
      } catch {
        window.location.hash = '';
      }

      try {
        const res = await fetch('/api/impersonation/accept', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ token }),
          cache: 'no-store',
        });
        if (!res.ok) {
          if (!cancelled) setPhase('error');
          return;
        }
        // Full document load so the cookie-gated bearer + banner initialise
        // cleanly against the tenant app.
        window.location.replace('/');
      } catch {
        if (!cancelled) setPhase('error');
      }
    }

    void run();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center bg-surface-alt px-6">
      <div className="max-w-md w-full text-center">
        {phase === 'starting' ? (
          <>
            <p className="text-h2 text-navy">Starting inspection…</p>
            <p className="mt-2 text-body text-slate">
              Opening this account in read-only staff view.
            </p>
          </>
        ) : (
          <>
            <p className="text-h2 text-navy">Inspection link invalid</p>
            <p className="mt-2 text-body text-slate">
              This hand-off link is missing or has expired. Reopen the inspection
              from the operator console.
            </p>
          </>
        )}
      </div>
    </div>
  );
}
