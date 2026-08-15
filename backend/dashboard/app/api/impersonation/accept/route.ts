/**
 * Accept an operator impersonation hand-off, server-side.
 *
 * The operator console opens `‹dashboard-origin›/inspect#‹urlencoded token›` —
 * the token rides in the URL *fragment*, which is never sent to a server or
 * written to an access log. The /inspect client reads it and POSTs it here so it
 * lands in an HttpOnly cookie that page scripts cannot read, out of the URL and
 * out of history.
 *
 * This route is deliberately outside the session gate (see middleware): the
 * operator has no tenant NextAuth session, only this hand-off.
 */

import { NextResponse, type NextRequest } from 'next/server';
import {
  IMPERSONATION_COOKIE,
  IMPERSONATION_MARKER_COOKIE,
} from '@/lib/impersonation-cookies';

/** Absolute cap on how long the cookie may live, even if the token claims more. */
const MAX_TTL_SECONDS = 60 * 60; // 1 hour
/** Floor so a nearly-expired token still gives the operator a usable window. */
const MIN_TTL_SECONDS = 30;
/** Fallback when the token carries no `exp` claim. */
const DEFAULT_TTL_SECONDS = 15 * 60;

/** Decode a JWT payload without verifying (the tenant API is the verifier). */
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

export async function POST(request: NextRequest): Promise<NextResponse> {
  let body: { token?: unknown };
  try {
    body = (await request.json()) as { token?: unknown };
  } catch {
    return NextResponse.json({ error: 'invalid_json' }, { status: 400 });
  }

  const token = typeof body.token === 'string' ? body.token.trim() : '';
  if (!token) {
    return NextResponse.json({ error: 'token_required' }, { status: 400 });
  }

  const claims = decodeClaims(token);
  if (!claims) {
    return NextResponse.json({ error: 'malformed_token' }, { status: 400 });
  }
  // Only ever accept a token minted for this purpose — never a tenant access
  // token pasted in by mistake, which would carry mutation rights.
  if (claims.typ !== 'impersonation') {
    return NextResponse.json({ error: 'not_an_impersonation_token' }, { status: 400 });
  }

  const expSeconds = typeof claims.exp === 'number' ? claims.exp : null;
  const nowSeconds = Math.floor(Date.now() / 1000);
  if (expSeconds !== null && expSeconds <= nowSeconds) {
    return NextResponse.json({ error: 'token_expired' }, { status: 400 });
  }
  const ttlSeconds =
    expSeconds !== null
      ? Math.min(MAX_TTL_SECONDS, Math.max(MIN_TTL_SECONDS, expSeconds - nowSeconds))
      : DEFAULT_TTL_SECONDS;

  const secure = process.env.NODE_ENV === 'production';
  const response = NextResponse.json({ ok: true });
  // The raw token — server-only, XSS-resistant.
  response.cookies.set(IMPERSONATION_COOKIE, token, {
    httpOnly: true,
    secure,
    // Lax: the hand-off arrives as a top-level cross-site GET navigation from the
    // console, and the follow-up POST from /inspect is same-origin.
    sameSite: 'lax',
    path: '/',
    maxAge: ttlSeconds,
  });
  // The client-readable marker — presence only, no token material.
  response.cookies.set(IMPERSONATION_MARKER_COOKIE, '1', {
    httpOnly: false,
    secure,
    sameSite: 'lax',
    path: '/',
    maxAge: ttlSeconds,
  });
  return response;
}
