/**
 * Report the current impersonation hand-off to the client.
 *
 * The impersonation token lives in an HttpOnly cookie the browser cannot read,
 * yet the API client attaches the bearer client-side (it calls the tenant API
 * directly). So this route is the single, controlled seam that hands the token
 * value back to same-origin page scripts for use as the bearer — the same trust
 * level at which NextAuth already exposes the tenant access token via its own
 * session endpoint. The durable credential stays HttpOnly (no document.cookie
 * theft); only a live, in-memory copy reaches the page.
 *
 * Expiry is computed here from the token's own `exp`: an expired hand-off is
 * reported as `expired` with a null token, never handed out for another call.
 */

import { NextResponse } from 'next/server';
import { cookies } from 'next/headers';
import { IMPERSONATION_COOKIE } from '@/lib/impersonation-cookies';

interface ImpersonationStatus {
  impersonating: boolean;
  expired: boolean;
  token: string | null;
  operator: string | null;
  org: string | null;
  expiresAt: number | null;
}

const IDLE: ImpersonationStatus = {
  impersonating: false,
  expired: false,
  token: null,
  operator: null,
  org: null,
  expiresAt: null,
};

function decodeClaims(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) return null;
  try {
    return JSON.parse(Buffer.from(parts[1], 'base64url').toString()) as Record<
      string,
      unknown
    >;
  } catch {
    return null;
  }
}

function firstString(
  claims: Record<string, unknown>,
  keys: string[],
): string | null {
  for (const key of keys) {
    const value = claims[key];
    if (typeof value === 'string' && value) return value;
  }
  return null;
}

export async function GET(): Promise<NextResponse> {
  const token = (await cookies()).get(IMPERSONATION_COOKIE)?.value ?? null;
  if (!token) {
    return NextResponse.json(IDLE, {
      headers: { 'Cache-Control': 'no-store' },
    });
  }

  const claims = decodeClaims(token);
  const operator = claims
    ? firstString(claims, ['operator', 'operator_email', 'actor', 'act', 'email', 'sub'])
    : null;
  const org = claims ? firstString(claims, ['org', 'organization', 'organization_id']) : null;
  const expSeconds = claims && typeof claims.exp === 'number' ? claims.exp : null;
  const expiresAt = expSeconds !== null ? expSeconds * 1000 : null;
  const expired = expiresAt !== null && expiresAt <= Date.now();

  const status: ImpersonationStatus = {
    impersonating: true,
    expired,
    token: expired ? null : token,
    operator,
    org,
    expiresAt,
  };
  return NextResponse.json(status, { headers: { 'Cache-Control': 'no-store' } });
}
