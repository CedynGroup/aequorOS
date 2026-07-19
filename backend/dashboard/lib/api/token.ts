/**
 * Holds the current backend access token for the API client (client-side).
 *
 * The generated API client runs in the browser and attaches this as
 * `Authorization: Bearer`. A SessionProvider effect keeps it in sync with the
 * NextAuth session, so the token is never hard-coded and follows sign-in/out.
 *
 * The cache is expiry-aware: once the token is within `SKEW_MS` of its `exp`, it
 * is treated as a miss so the client falls back to `getSession()`, which triggers
 * NextAuth's silent refresh (see auth.ts) rather than replaying a stale token.
 */
let accessToken: string | null = null;
let accessTokenExpiresAt = 0; // epoch ms; 0 = unknown/never-valid

// Consider the token spent a bit before its real expiry so an in-flight request
// never races the boundary.
const SKEW_MS = 30_000;

/** Decode a JWT `exp` (seconds) to epoch ms; 0 if absent/unparseable. */
function expiryMs(token: string): number {
  try {
    const part = token.split('.')[1];
    const json = atob(part.replace(/-/g, '+').replace(/_/g, '/'));
    const exp = (JSON.parse(json) as { exp?: number }).exp;
    return typeof exp === 'number' ? exp * 1000 : 0;
  } catch {
    return 0;
  }
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
  accessTokenExpiresAt = token ? expiryMs(token) : 0;
}

/** The cached token, or null if absent or within `SKEW_MS` of expiry. */
export function getAccessToken(): string | null {
  if (!accessToken) return null;
  if (accessTokenExpiresAt && Date.now() > accessTokenExpiresAt - SKEW_MS) return null;
  return accessToken;
}
