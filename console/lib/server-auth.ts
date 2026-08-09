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
 *    EVERY request (zero-trust, app/core/security.verify_oidc_id_token).
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

/** Established workforce session. The id_token stays HttpOnly-server-side. */
export interface OperatorSession {
  id_token: string;
  email: string;
  /** Unix seconds — mirrors the id_token exp; the API is the authority. */
  exp: number;
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
  if (!session?.id_token || !session.email || typeof session.exp !== 'number') return null;
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
