/**
 * GET /api/auth/callback — complete the workforce OIDC code flow.
 *
 * Validates state against the transaction cookie, exchanges the code (with
 * the PKCE verifier and, for confidential clients, the client secret), does
 * the relying-party checks (nonce, expiry, email present), and establishes
 * the HttpOnly session cookie holding the id_token.
 *
 * Deliberately NOT verified cryptographically here: the operator API is the
 * zero-trust verifier (signature/issuer/audience/expiry via issuer JWKS) on
 * every proxied request — a forged cookie buys nothing but 401s.
 */

import { NextResponse, type NextRequest } from 'next/server';
import {
  OAUTH_TXN_COOKIE,
  SESSION_COOKIE,
  decodeCookie,
  discover,
  encodeCookie,
  oidcEnv,
  unverifiedJwtPayload,
  type OauthTxn,
} from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

function failure(req: NextRequest, code: string): NextResponse {
  const res = NextResponse.redirect(new URL(`/login?error=${code}`, req.url));
  res.cookies.delete(OAUTH_TXN_COOKIE);
  return res;
}

export async function GET(req: NextRequest) {
  const env = oidcEnv();
  if (!env) return failure(req, 'oidc_not_configured');

  const code = req.nextUrl.searchParams.get('code');
  const state = req.nextUrl.searchParams.get('state');
  const txn = decodeCookie<OauthTxn>(req.cookies.get(OAUTH_TXN_COOKIE)?.value);
  if (!code || !state || !txn) return failure(req, 'missing_transaction');
  if (state !== txn.state) return failure(req, 'state_mismatch');

  let tokenEndpoint: string;
  try {
    tokenEndpoint = (await discover(env.issuer)).token_endpoint;
  } catch {
    return failure(req, 'idp_unreachable');
  }

  const form = new URLSearchParams({
    grant_type: 'authorization_code',
    code,
    redirect_uri: txn.redirect_uri,
    client_id: env.clientId,
    code_verifier: txn.verifier,
  });
  if (env.clientSecret) form.set('client_secret', env.clientSecret);

  let idToken: string;
  try {
    const tokenRes = await fetch(tokenEndpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
      cache: 'no-store',
    });
    if (!tokenRes.ok) return failure(req, 'token_exchange_failed');
    const body = (await tokenRes.json()) as { id_token?: unknown };
    if (typeof body.id_token !== 'string' || !body.id_token) {
      return failure(req, 'no_id_token');
    }
    idToken = body.id_token;
  } catch {
    return failure(req, 'token_exchange_failed');
  }

  const claims = unverifiedJwtPayload(idToken);
  if (!claims) return failure(req, 'malformed_id_token');
  if (claims.nonce !== txn.nonce) return failure(req, 'nonce_mismatch');
  const email = claims.email;
  const exp = claims.exp;
  if (typeof email !== 'string' || !email) return failure(req, 'no_email_claim');
  if (typeof exp !== 'number' || exp * 1000 <= Date.now()) return failure(req, 'expired');

  const res = NextResponse.redirect(new URL('/tenants', req.url));
  res.cookies.delete(OAUTH_TXN_COOKIE);
  res.cookies.set(
    SESSION_COOKIE,
    encodeCookie({ id_token: idToken, email: email.toLowerCase(), exp }),
    {
      httpOnly: true,
      sameSite: 'lax',
      secure: req.nextUrl.protocol === 'https:',
      path: '/',
      // Session dies with the id_token — the operator API would 401 an
      // expired token anyway; the cookie just stops pretending earlier.
      maxAge: Math.max(1, Math.min(exp - Math.floor(Date.now() / 1000), 12 * 3600)),
    },
  );
  return res;
}
