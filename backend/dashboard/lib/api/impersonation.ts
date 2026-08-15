/**
 * Client-side state for act-as-examiner (operator inspection).
 *
 * A framework-free external store (subscribed to via useSyncExternalStore in
 * useImpersonation) so BOTH React components and the non-React API-client bearer
 * selector can read the same source of truth.
 *
 * INVARIANT — strictly additive, gated on the marker cookie. When
 * `aeq-impersonation-active` is absent, every function here short-circuits
 * without a network call, the state stays IDLE, and the API bearer selection is
 * byte-identical to the normal tenant login path.
 *
 * The durable token lives in an HttpOnly cookie the browser cannot read; the raw
 * value is fetched — once, then cached — from the server route
 * `/api/impersonation/status`, which is also where expiry is computed from the
 * token's own `exp`.
 */

import { IMPERSONATION_MARKER_COOKIE } from '@/lib/impersonation-cookies';

export interface ImpersonationState {
  /** A staff inspection hand-off is active on this browser. */
  impersonating: boolean;
  /** The hand-off token has expired (or was rejected) — reads will 401. */
  expired: boolean;
  /** The raw impersonation JWT to use as the API bearer, or null when unusable. */
  token: string | null;
  /** Best-effort operator identity for display (from the token's claims). */
  operator: string | null;
  /** The tenant org id being inspected (from the token's claims). */
  org: string | null;
  /** Epoch ms the token expires, for the client-side "ended" timer. */
  expiresAt: number | null;
}

const IDLE: ImpersonationState = {
  impersonating: false,
  expired: false,
  token: null,
  operator: null,
  org: null,
  expiresAt: null,
};

let state: ImpersonationState = IDLE;
let inflight: Promise<ImpersonationState> | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function setState(next: ImpersonationState): void {
  state = next;
  emit();
}

export function subscribeImpersonation(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getImpersonationSnapshot(): ImpersonationState {
  return state;
}

/** SSR snapshot: the server never has the browser marker, so always IDLE. */
export function getImpersonationServerSnapshot(): ImpersonationState {
  return IDLE;
}

/**
 * Cheap, synchronous test: is an inspection hand-off active on this browser?
 * Reads only the non-HttpOnly marker cookie — no token, no network. Returns
 * false on the server and, crucially, on every normal (non-impersonation)
 * session, where it is the first and only check the bearer selector runs.
 */
export function impersonationMarkerPresent(): boolean {
  if (typeof document === 'undefined') return false;
  return document.cookie
    .split('; ')
    .some((entry) => entry.startsWith(`${IMPERSONATION_MARKER_COOKIE}=`));
}

/** Fetch (once, then cache) the hand-off status from the server route. */
export async function refreshImpersonationStatus(): Promise<ImpersonationState> {
  if (typeof window === 'undefined') return IDLE;
  if (!impersonationMarkerPresent()) {
    if (state !== IDLE) setState(IDLE);
    return IDLE;
  }
  if (inflight) return inflight;
  inflight = (async () => {
    try {
      const res = await fetch('/api/impersonation/status', { cache: 'no-store' });
      if (!res.ok) {
        setState(IDLE);
        return IDLE;
      }
      const body = (await res.json()) as ImpersonationState;
      setState(body);
      return body;
    } catch {
      // Transient network error — keep whatever we last knew rather than
      // dropping the operator out of inspection.
      return state;
    } finally {
      inflight = null;
    }
  })();
  return inflight;
}

/**
 * The bearer to use for API calls while inspecting, or null when there is no
 * (usable) hand-off. The marker gate means this returns null instantly on the
 * normal path; only an active hand-off ever triggers the one-time status fetch.
 */
export async function getImpersonationBearer(): Promise<string | null> {
  if (!impersonationMarkerPresent()) return null;
  let current = state;
  if (!current.impersonating && !current.expired) {
    current = await refreshImpersonationStatus();
  }
  if (current.impersonating && !current.expired && current.token) {
    return current.token;
  }
  return null;
}

/**
 * Flip to the "inspection ended" state. Driven by the token's own expiry timer
 * (see ImpersonationBanner) and by any API 401 observed while inspecting (see
 * the client middleware). The token is dropped so no further call replays it.
 */
export function markImpersonationExpired(): void {
  if (state.impersonating && !state.expired) {
    setState({ ...state, expired: true, token: null });
  }
}

/** Clear the hand-off cookies server-side and reset the store to IDLE. */
export async function leaveImpersonation(): Promise<void> {
  try {
    await fetch('/api/impersonation/leave', { method: 'POST', cache: 'no-store' });
  } catch {
    // Best-effort: even if the network call fails, drop local state so the UI
    // stops treating this as an active inspection.
  }
  setState(IDLE);
}
