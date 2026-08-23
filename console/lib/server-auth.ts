/**
 * Server-side workforce OIDC machinery for the operator console.
 *
 * The console is a CONFIDENTIAL client running the authorization-code flow
 * with PKCE against the workforce IdP (Google Workspace / Okta — the same
 * issuer + client id the operator API verifies with, so the two apps share
 * one OPERATOR_OIDC_* credential set). Two invariants, both matching the
 * platform's SSO philosophy (dashboard attestation step-up precedent):
 *
 * 1. The id_token NEVER reaches browser JavaScript. It lives in an HttpOnly
 *    cookie and is attached to operator API calls by the /api/op proxy.
 * 2. The console does NOT claim to verify the token cryptographically — the
 *    operator API independently verifies signature/issuer/audience/expiry on
 *    EVERY request (zero-trust, app/core/security.verify_oidc_id_token), then
 *    requires the verified email to match an active provisioned operator_users
 *    row and takes authorization from that row.
 *    The callback checks only what the relying party must: state, nonce,
 *    expiry, and that an email claim exists.
 *
 * Env (console side, server-only — never NEXT_PUBLIC):
 *   OPERATOR_OIDC_ISSUER        e.g. https://accounts.google.com
 *   OPERATOR_OIDC_CLIENT_ID     must equal the backend's OPERATOR_OIDC_CLIENT_ID
 *   OPERATOR_OIDC_CLIENT_SECRET the web-client secret (Google requires it even
 *                               with PKCE for confidential web clients)
 *   OPERATOR_API_URL            proxy target, default http://127.0.0.1:8100
 *   CONSOLE_BASE_URL            optional; overrides the redirect_uri origin
 *                               when the console sits behind a proxy
 */

import type { NextRequest } from 'next/server';

export interface OidcEnv {
  issuer: string;
  clientId: string;
  clientSecret: string | null;
}

export function oidcEnv(): OidcEnv | null {
  const issuer = process.env.OPERATOR_OIDC_ISSUER?.trim();
  const clientId = process.env.OPERATOR_OIDC_CLIENT_ID?.trim();
  if (!issuer || !clientId) return null;
  return {
    issuer: issuer.replace(/\/+$/, ''),
    clientId,
    clientSecret: process.env.OPERATOR_OIDC_CLIENT_SECRET?.trim() || null,
  };
}

export function operatorApiUrl(): string {
  return (process.env.OPERATOR_API_URL ?? 'http://127.0.0.1:8100').replace(/\/+$/, '');
}

/** Origin used to build redirect_uri: explicit override, else the request's. */
export function consoleBaseUrl(req: NextRequest): string {
  const override = process.env.CONSOLE_BASE_URL?.trim();
  if (override) return override.replace(/\/+$/, '');
  return req.nextUrl.origin;
}

// ---------------------------------------------------------------------------
// OIDC discovery (cached per issuer for the process lifetime)
// ---------------------------------------------------------------------------

export interface DiscoveryDocument {
  authorization_endpoint: string;
  token_endpoint: string;
}

const discoveryCache = new Map<string, DiscoveryDocument>();

export async function discover(issuer: string): Promise<DiscoveryDocument> {
  const cached = discoveryCache.get(issuer);
  if (cached) return cached;
  const res = await fetch(`${issuer}/.well-known/openid-configuration`, {
    cache: 'no-store',
  });
  if (!res.ok) {
    throw new Error(`OIDC discovery failed for ${issuer}: HTTP ${res.status}`);
  }
  const doc = (await res.json()) as Partial<DiscoveryDocument>;
  if (
    typeof doc.authorization_endpoint !== 'string' ||
    typeof doc.token_endpoint !== 'string'
  ) {
    throw new Error(`OIDC discovery for ${issuer} returned no usable endpoints.`);
  }
  const usable = {
    authorization_endpoint: doc.authorization_endpoint,
    token_endpoint: doc.token_endpoint,
  };
  discoveryCache.set(issuer, usable);
  return usable;
}

// ---------------------------------------------------------------------------
// Crypto helpers (WebCrypto — available in Next.js route handlers)
// ---------------------------------------------------------------------------

export function randomUrlSafe(bytes: number): string {
  const buf = new Uint8Array(bytes);
  crypto.getRandomValues(buf);
  return b64url(buf);
}

/** PKCE S256 code challenge for a verifier. */
export async function s256(verifier: string): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return b64url(new Uint8Array(digest));
}

function b64url(buf: Uint8Array): string {
  return Buffer.from(buf).toString('base64url');
}

// ---------------------------------------------------------------------------
// Cookie payloads
// ---------------------------------------------------------------------------

export const OAUTH_TXN_COOKIE = 'op_oauth';
export const SESSION_COOKIE = 'op_session';

/** In-flight authorization transaction (state/nonce/PKCE verifier). */
export interface OauthTxn {
  state: string;
  nonce: string;
  verifier: string;
  redirect_uri: string;
}

/**
 * Established operator session, in either auth mode. The credential stays
 * HttpOnly-server-side in both:
 * - password mode: `token` holds the operator session JWT minted by
 *   POST /operator/auth/login (`mode: 'password'`);
 * - workforce SSO mode: `id_token` holds the OIDC id_token (`mode: 'oidc'`;
 *   also the shape of pre-password-era cookies, where `mode` is absent).
 * The /api/op proxy forwards whichever credential is present as the bearer;
 * the operator API is the verifying authority for both.
 */
export interface OperatorSession {
  /** Operator session JWT (password sign-in). */
  token?: string;
  /** Workforce OIDC id_token (SSO sign-in). */
  id_token?: string;
  email: string;
  /** Unix seconds — mirrors the credential's exp; the API is the authority. */
  exp: number;
  mode?: 'password' | 'oidc';
}

/** The bearer the /api/op proxy forwards — operator JWT or OIDC id_token. */
export function sessionBearer(session: OperatorSession): string | null {
  return session.token ?? session.id_token ?? null;
}

/** Which sign-in produced this session (legacy cookies predate `mode`). */
export function sessionMode(session: OperatorSession): 'password' | 'oidc' {
  return session.mode ?? (session.token ? 'password' : 'oidc');
}

export function encodeCookie(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString('base64url');
}

export function decodeCookie<T>(raw: string | undefined): T | null {
  if (!raw) return null;
  try {
    return JSON.parse(Buffer.from(raw, 'base64url').toString()) as T;
  } catch {
    return null;
  }
}

export function readSession(req: NextRequest): OperatorSession | null {
  const session = decodeCookie<OperatorSession>(req.cookies.get(SESSION_COOKIE)?.value);
  if (!session || !session.email || typeof session.exp !== 'number') return null;
  if (!sessionBearer(session)) return null;
  if (session.exp * 1000 <= Date.now()) return null;
  return session;
}

/** Decode a JWT payload WITHOUT verification (the operator API verifies). */
export function unverifiedJwtPayload(jwt: string): Record<string, unknown> | null {
  const parts = jwt.split('.');
  if (parts.length !== 3) return null;
  try {
    return JSON.parse(Buffer.from(parts[1], 'base64url').toString()) as Record<string, unknown>;
  } catch {
    return null;
  }
}
