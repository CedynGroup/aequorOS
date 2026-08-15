/**
 * POST /api/auth/password-login — staff email+password sign-in (primary path).
 *
 * Relays the credentials server-side to POST ${OPERATOR_API_URL}/operator/auth/login
 * and, on success, establishes the SAME HttpOnly op_session cookie the SSO
 * callback sets — with the operator session JWT in `token` instead of an
 * `id_token`, and `mode: 'password'`. The /api/op proxy forwards it as the
 * bearer identically; browser JavaScript never holds the credential.
 *
 * Failures pass the backend envelope through untouched: the 401 stays the
 * backend's generic "Invalid email or password." (no user enumeration), 429
 * is the login throttle, 503 is the unset-OPERATOR_JWT_SECRET refusal.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { SESSION_COOKIE, encodeCookie, operatorApiUrl } from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

interface LoginUpstream {
  access_token?: unknown;
  expires_at?: unknown;
  operator?: { email?: unknown; display_name?: unknown; role?: unknown };
}

export async function POST(req: NextRequest) {
  let body: { email?: unknown; password?: unknown };
  try {
    body = (await req.json()) as { email?: unknown; password?: unknown };
  } catch {
    return NextResponse.json(
      { error: { code: 'invalid_request', message: 'Malformed request body.' } },
      { status: 400 },
    );
  }
  const email = typeof body.email === 'string' ? body.email.trim().toLowerCase() : '';
  const password = typeof body.password === 'string' ? body.password : '';
  if (!email || !password) {
    return NextResponse.json(
      { error: { code: 'invalid_request', message: 'Email and password are required.' } },
      { status: 400 },
    );
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${operatorApiUrl()}/operator/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({ email, password }),
      cache: 'no-store',
    });
  } catch {
    return NextResponse.json(
      {
        error: {
          code: 'operator_api_unreachable',
          message: `The console could not reach the operator API at ${operatorApiUrl()}.`,
        },
      },
      { status: 502 },
    );
  }

  if (!upstream.ok) {
    // Pass the backend envelope (and its deliberately generic 401) through.
    const text = await upstream.text();
    return new NextResponse(text, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' },
    });
  }

  let payload: LoginUpstream;
  try {
    payload = (await upstream.json()) as LoginUpstream;
  } catch {
    return NextResponse.json(
      { error: { code: 'malformed_response', message: 'The operator API returned an unreadable response.' } },
      { status: 502 },
    );
  }
  const token = payload.access_token;
  const expiresAt = payload.expires_at;
  const sessionEmail =
    typeof payload.operator?.email === 'string' ? payload.operator.email : email;
  const exp = typeof expiresAt === 'string' ? Math.floor(Date.parse(expiresAt) / 1000) : NaN;
  if (typeof token !== 'string' || !token || !Number.isFinite(exp)) {
    return NextResponse.json(
      { error: { code: 'malformed_response', message: 'The operator API returned no usable session.' } },
      { status: 502 },
    );
  }

  const res = NextResponse.json({
    ok: true,
    email: sessionEmail,
    role: typeof payload.operator?.role === 'string' ? payload.operator.role : null,
  });
  res.cookies.set(
    SESSION_COOKIE,
    encodeCookie({ token, email: sessionEmail, exp, mode: 'password' }),
    {
      httpOnly: true,
      sameSite: 'lax',
      secure: req.nextUrl.protocol === 'https:',
      path: '/',
      // Cookie dies with the JWT — the operator API would 401 an expired
      // token anyway; the cookie just stops pretending earlier.
      maxAge: Math.max(1, Math.min(exp - Math.floor(Date.now() / 1000), 12 * 3600)),
    },
  );
  return res;
}
