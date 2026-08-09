/**
 * GET /api/auth/login — start the workforce OIDC authorization-code flow.
 *
 * Builds the IdP authorize URL (PKCE S256 + state + nonce) and stashes the
 * transaction in a short-lived HttpOnly cookie scoped to /api/auth. The
 * browser never sees any of the values it could be tricked into replaying.
 */

import { NextResponse, type NextRequest } from 'next/server';
import {
  OAUTH_TXN_COOKIE,
  consoleBaseUrl,
  discover,
  encodeCookie,
  oidcEnv,
  randomUrlSafe,
  s256,
} from '@/lib/server-auth';

export const dynamic = 'force-dynamic';

export async function GET(req: NextRequest) {
  const env = oidcEnv();
  if (!env) {
    return NextResponse.redirect(new URL('/login?error=oidc_not_configured', req.url));
  }

  let authorizationEndpoint: string;
  try {
    authorizationEndpoint = (await discover(env.issuer)).authorization_endpoint;
  } catch {
    return NextResponse.redirect(new URL('/login?error=idp_unreachable', req.url));
  }

  const state = randomUrlSafe(32);
  const nonce = randomUrlSafe(32);
  const verifier = randomUrlSafe(48);
  const redirectUri = `${consoleBaseUrl(req)}/api/auth/callback`;

  const url = new URL(authorizationEndpoint);
  url.searchParams.set('response_type', 'code');
  url.searchParams.set('client_id', env.clientId);
  url.searchParams.set('redirect_uri', redirectUri);
  url.searchParams.set('scope', 'openid email profile');
  url.searchParams.set('state', state);
  url.searchParams.set('nonce', nonce);
  url.searchParams.set('code_challenge', await s256(verifier));
  url.searchParams.set('code_challenge_method', 'S256');

  const res = NextResponse.redirect(url);
  res.cookies.set(
    OAUTH_TXN_COOKIE,
    encodeCookie({ state, nonce, verifier, redirect_uri: redirectUri }),
    {
      httpOnly: true,
      sameSite: 'lax',
      secure: req.nextUrl.protocol === 'https:',
      path: '/api/auth',
      maxAge: 600,
    },
  );
  return res;
}
