'use client';

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { listInspectorSessions, type InspectorSession } from './api';

/**
 * Tenant Inspector session context. The AppShell mounts the provider so the
 * ImpersonationBanner and any feature page can read the currently-active
 * cross-tenant inspection session. Feature pages that OPEN a session (via
 * startInspectorSession) push it in with `setActive`; pages that end one call
 * `setActive(null)` (or `refresh()`).
 */
export interface InspectorContextValue {
  /** The active (non-expired) inspector session, or null. */
  active: InspectorSession | null;
  /** Every active session the operator currently holds. */
  sessions: InspectorSession[];
  loading: boolean;
  /** Set/clear the active session locally (after start/end). */
  setActive: (session: InspectorSession | null) => void;
  /** Re-hydrate from the API. */
  refresh: () => void;
}

const InspectorContext = createContext<InspectorContextValue | null>(null);

/** True when the session has not yet passed its `expires_at`. */
function isLive(session: InspectorSession): boolean {
  const exp = Date.parse(session.expires_at);
  return Number.isNaN(exp) ? true : exp > Date.now();
}

export function InspectorProvider({ children }: { children: ReactNode }) {
  const [sessions, setSessions] = useState<InspectorSession[]>([]);
  const [active, setActiveState] = useState<InspectorSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  const refresh = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    listInspectorSessions({ active: true })
      .then((res) => {
        if (!alive) return;
        const live = (res.sessions ?? []).filter(isLive);
        setSessions(live);
        setActiveState((prev) => {
          // Prefer the still-live version of the current active session.
          if (prev) {
            const match = live.find((s) => s.session_id === prev.session_id);
            if (match) return match;
          }
          return live[0] ?? null;
        });
        setLoading(false);
      })
      .catch(() => {
        // Inspector endpoints not yet live (backend agent builds them) — the
        // banner simply stays hidden. Never crash the shell over this.
        if (!alive) return;
        setSessions([]);
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  const setActive = useCallback((session: InspectorSession | null) => {
    setActiveState(session);
    if (session) {
      setSessions((prev) => {
        const rest = prev.filter((s) => s.session_id !== session.session_id);
        return isLive(session) ? [session, ...rest] : rest;
      });
    }
  }, []);

  const value = useMemo<InspectorContextValue>(
    () => ({
      active: active && isLive(active) ? active : null,
      sessions,
      loading,
      setActive,
      refresh,
    }),
    [active, sessions, loading, setActive, refresh],
  );

  return <InspectorContext.Provider value={value}>{children}</InspectorContext.Provider>;
}

/**
 * Read the inspector context. Safe outside a provider (returns an inert
 * value) so a component never crashes for lack of one.
 */
export function useInspector(): InspectorContextValue {
  const ctx = useContext(InspectorContext);
  if (ctx) return ctx;
  return {
    active: null,
    sessions: [],
    loading: false,
    setActive: () => {},
    refresh: () => {},
  };
}
