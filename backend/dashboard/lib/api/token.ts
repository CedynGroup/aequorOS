/**
 * Holds the current backend access token for the API client (client-side).
 *
 * The generated API client runs in the browser and attaches this as
 * `Authorization: Bearer`. A SessionProvider effect keeps it in sync with the
 * NextAuth session, so the token is never hard-coded and follows sign-in/out.
 */
let accessToken: string | null = null;

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}
