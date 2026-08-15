'use client';

/**
 * Persistent, un-dismissable staff-inspection banner.
 *
 * Rendered app-wide (mounted in Providers). It renders NOTHING unless an
 * inspection hand-off is active, so on a normal tenant session it is inert —
 * the priming fetch short-circuits on the missing marker cookie and the fixed
 * bar and its body-padding side effect never appear.
 *
 * While inspecting it is fixed to the top in an alarming amber (red once the
 * session has ended), can't be dismissed, and always offers a way out so the
 * operator is never trapped.
 */

import { useEffect, useState } from 'react';
import { AlertTriangle, Loader2, LogOut } from 'lucide-react';
import { useImpersonation } from './useImpersonation';
import {
  leaveImpersonation,
  markImpersonationExpired,
  refreshImpersonationStatus,
} from '@/lib/api/impersonation';
import { LOGIN_URL } from '@/lib/loginUrl';

/** Height of the fixed bar; also the body offset applied while it shows. */
const BANNER_HEIGHT = '2.5rem';

export default function ImpersonationBanner() {
  const state = useImpersonation();
  const [leaving, setLeaving] = useState(false);

  // Prime the store once. No-op when no hand-off marker is present, so a normal
  // session issues no extra request and the store stays IDLE.
  useEffect(() => {
    void refreshImpersonationStatus();
  }, []);

  // Flip to the "ended" state exactly when the token's own expiry passes, so the
  // operator gets a clear message instead of silently failing reads.
  useEffect(() => {
    if (!state.impersonating || state.expired || !state.expiresAt) return;
    const remaining = state.expiresAt - Date.now();
    if (remaining <= 0) {
      markImpersonationExpired();
      return;
    }
    const timer = setTimeout(() => markImpersonationExpired(), remaining);
    return () => clearTimeout(timer);
  }, [state.impersonating, state.expired, state.expiresAt]);

  // Keep the app clear of the fixed bar. Only touches the DOM while inspecting.
  useEffect(() => {
    if (!state.impersonating) return;
    const previous = document.body.style.paddingTop;
    document.body.style.paddingTop = BANNER_HEIGHT;
    return () => {
      document.body.style.paddingTop = previous;
    };
  }, [state.impersonating]);

  if (!state.impersonating) return null;

  const ended = state.expired;

  const onLeave = async () => {
    setLeaving(true);
    await leaveImpersonation();
    // Send staff back out of the tenant app entirely rather than to a page that
    // would immediately 401 without a hand-off token or a tenant session.
    window.location.href = LOGIN_URL;
  };

  return (
    <div
      role="alert"
      aria-live="assertive"
      className="fixed inset-x-0 top-0 z-[100] flex items-center justify-center gap-3 px-4 text-caption font-semibold text-white shadow-md"
      style={{ height: BANNER_HEIGHT, backgroundColor: ended ? '#b91c1c' : '#b45309' }}
    >
      <AlertTriangle size={14} aria-hidden className="shrink-0" />
      <span className="truncate">
        {ended ? (
          'Inspection session ended — this hand-off has expired. Leave to close it.'
        ) : (
          <>
            AequorOS staff is inspecting this account — read-only.
            {state.operator ? (
              <span className="hidden sm:inline font-normal opacity-90">
                {' '}
                Operator: {state.operator}
                {state.org ? ` · ${state.org}` : ''}
              </span>
            ) : null}
          </>
        )}
      </span>
      <button
        type="button"
        onClick={onLeave}
        disabled={leaving}
        className="shrink-0 inline-flex items-center gap-1.5 rounded bg-white/15 px-2.5 py-1 font-semibold hover:bg-white/25 disabled:opacity-60"
      >
        {leaving ? (
          <Loader2 size={12} className="animate-spin" aria-hidden />
        ) : (
          <LogOut size={12} aria-hidden />
        )}
        Leave inspection
      </button>
    </div>
  );
}
