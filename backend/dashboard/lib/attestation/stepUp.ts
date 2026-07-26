/**
 * Server-side OIDC step-up for the signing ceremony.
 *
 * Act 772 asks that a signature be created by means under the signatory's
 * control. A server-held key used on the strength of an ambient session token is
 * a weak answer; proving the signer is present *now* is the strongest control we
 * can offer short of signer-held hardware (design §3.4, legal item L1).
 *
 * Everything here runs in the Next.js server runtime. Two secrets exist in this
 * flow and NEITHER reaches the browser:
 *
 *   · the fresh `id_token` from the bank's IdP — exchanged server-side and
 *     forwarded straight to the risk service, never serialised into a session;
 *   · the single-use signing authorisation — held in an HttpOnly cookie and
 *     spent by a server route, so a browser script cannot read or replay it.
 *
 * That is why the certify call is proxied rather than made from the client.
 */

import { createHash, randomBytes } from 'crypto';
import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

/** Correlates the authorize redirect with its callback. HttpOnly, short-lived. */
export const STATE_COOKIE = 'aeq-stepup-state';
/** Holds the single-use signing authorisation between callback and certify. */
export const AUTHORIZATION_COOKIE = 'aeq-stepup-authorization';

const STATE_TTL_SECONDS = 600;

export interface StepUpContext {
  state: string;
  nonce: string;
  codeVerifier: string;
  bankId: string;
  packageId: string;
  signingRole: string;
  returnTo: string;
}

/**
 * Mirrors `SsoClientConfigResponse` — snake_case on the wire, because this route
 * is internal and deliberately outside the generated OpenAPI client.
 */
export interface OidcClientConfig {
  enabled: boolean;
  issuer?: string | null;
  client_id?: string | null;
  client_secret?: string | null;
}

interface OidcDiscovery {
  authorization_endpoint: string;
  token_endpoint: string;
}

/** The risk service base, `/api/v1` included — the same convention as auth.ts. */
export function apiBase(): string {
  return (
    process.env.RISK_API_INTERNAL_BASE_URL ??
    process.env.NEXT_PUBLIC_RISK_API_BASE_URL ??
    'http://127.0.0.1:8000/api/v1'
  );
}

/**
 * Read the `nonce` claim without verifying the signature. Safe and necessary:
 * OIDC nonce validation is by definition a claim comparison, and the token's
 * cryptographic verification is done by the risk service against the issuer's
 * JWKS before it is accepted for anything. Nothing here trusts the payload
 * beyond deciding whether to forward it at all.
 */
export function unverifiedNonce(idToken: string): string | null {
  try {
    const payload = idToken.split('.')[1];
    if (!payload) return null;
    const claims = JSON.parse(Buffer.from(payload, 'base64url').toString()) as {
      nonce?: unknown;
    };
    return typeof claims.nonce === 'string' ? claims.nonce : null;
  } catch {
    return null;
  }
}

/**
 * The OIDC client config, read through the risk service's internal-key-gated
 * endpoint — the single plaintext read path for the client secret, and the same
 * one NextAuth already uses for sign-in.
 *
 * "Not configured" is a valid answer, not an error: no internal key on this
 * deployment, or a backend that 404s the route because it has none either. Both
 * mean SSO is off, which the caller reports as such. Anything else throws, so a
 * broken backend is never mistaken for a deliberate absence of SSO.
 */
export async function fetchClientConfig(): Promise<OidcClientConfig> {
  const internalKey = process.env.SSO_INTERNAL_KEY;
  if (!internalKey) return { enabled: false };
  const response = await fetch(`${apiBase()}/auth/sso/client-config`, {
    headers: { 'X-Internal-Auth': internalKey },
    cache: 'no-store',
    signal: AbortSignal.timeout(5000),
  });
  if (response.status === 404) return { enabled: false };
  if (!response.ok) {
    throw new Error(`Could not read the SSO client config (${response.status}).`);
  }
  return (await response.json()) as OidcClientConfig;
}

export async function discover(issuer: string): Promise<OidcDiscovery> {
  const base = issuer.replace(/\/$/, '');
  const response = await fetch(`${base}/.well-known/openid-configuration`, {
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`OIDC discovery failed for ${base} (${response.status}).`);
  }
  const document = (await response.json()) as Partial<OidcDiscovery>;
  if (!document.authorization_endpoint || !document.token_endpoint) {
    throw new Error('OIDC discovery document is missing required endpoints.');
  }
  return {
    authorization_endpoint: document.authorization_endpoint,
    token_endpoint: document.token_endpoint,
  };
}

function base64Url(input: Buffer): string {
  return input.toString('base64url');
}

export function newPkcePair(): { verifier: string; challenge: string } {
  const verifier = base64Url(randomBytes(32));
  const challenge = base64Url(createHash('sha256').update(verifier).digest());
  return { verifier, challenge };
}

export function newOpaque(): string {
  return base64Url(randomBytes(32));
}

export async function writeStateCookie(context: StepUpContext): Promise<void> {
  const store = await cookies();
  store.set(STATE_COOKIE, JSON.stringify(context), {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    sameSite: 'lax', // the IdP redirect is a cross-site GET; 'strict' would drop it
    path: '/',
    maxAge: STATE_TTL_SECONDS,
  });
}

export async function readStateCookie(): Promise<StepUpContext | null> {
  const store = await cookies();
  const raw = store.get(STATE_COOKIE)?.value;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as StepUpContext;
  } catch {
    return null;
  }
}

export async function clearStateCookie(): Promise<void> {
  (await cookies()).delete(STATE_COOKIE);
}

export async function writeAuthorizationCookie(
  token: string,
  ttlSeconds: number,
): Promise<void> {
  const store = await cookies();
  store.set(AUTHORIZATION_COOKIE, token, {
    httpOnly: true,
    secure: process.env.NODE_ENV === 'production',
    // Strict is correct here: this cookie is only ever read by our own
    // same-site certify proxy, never by a cross-site navigation.
    sameSite: 'strict',
    path: '/',
    maxAge: Math.max(30, Math.min(ttlSeconds, 300)),
  });
}

export async function takeAuthorizationCookie(): Promise<string | null> {
  const store = await cookies();
  const token = store.get(AUTHORIZATION_COOKIE)?.value ?? null;
  if (token) store.delete(AUTHORIZATION_COOKIE);
  return token;
}

/** Exchange an authorization code for tokens. Runs server-side only. */
export async function exchangeCode(params: {
  tokenEndpoint: string;
  code: string;
  codeVerifier: string;
  clientId: string;
  clientSecret: string;
  redirectUri: string;
}): Promise<{ id_token?: string }> {
  const body = new URLSearchParams({
    grant_type: 'authorization_code',
    code: params.code,
    redirect_uri: params.redirectUri,
    client_id: params.clientId,
    client_secret: params.clientSecret,
    code_verifier: params.codeVerifier,
  });
  const response = await fetch(params.tokenEndpoint, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`Token exchange failed (${response.status}).`);
  }
  return (await response.json()) as { id_token?: string };
}

export function stepUpRedirectUri(origin: string): string {
  return `${origin}/api/attestation/step-up/callback`;
}

/**
 * Send the signer back to the ceremony with a status marker.
 *
 * Both step-up routes are navigation targets, so every outcome — success or
 * failure — has to land the signer back on a page. Returning JSON would dump a
 * raw error object into the address bar mid-ceremony. The marker is a fixed
 * vocabulary the dialog renders (`SSO_OUTCOMES`); the authorisation itself is
 * never in the URL.
 */
export function backToCeremony(
  origin: string,
  returnTo: string,
  outcome: string,
): NextResponse {
  // returnTo is validated as a relative path when the flow starts, and is read
  // from an HttpOnly cookie thereafter — a tampered query string cannot steer it.
  const target = new URL(returnTo, origin);
  target.searchParams.set('stepUp', outcome);
  return NextResponse.redirect(target.toString());
}
