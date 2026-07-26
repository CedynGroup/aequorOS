/**
 * Receive the IdP redirect, exchange the code server-side, and convert the
 * fresh id_token into a single-use signing authorisation.
 *
 * The id_token is created and consumed entirely within this request: it is
 * exchanged here, forwarded to the risk service (which verifies it against the
 * issuer's JWKS and checks `auth_time` freshness), and then dropped. It is never
 * written to a session, a log, or a redirect.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { auth } from '@/auth';
import {
  apiBase,
  backToCeremony as back,
  clearStateCookie,
  discover,
  exchangeCode,
  fetchClientConfig,
  readStateCookie,
  stepUpRedirectUri,
  unverifiedNonce,
  writeAuthorizationCookie,
} from '@/lib/attestation/stepUp';

export async function GET(request: NextRequest): Promise<NextResponse> {
  const origin = request.nextUrl.origin;
  const session = await auth();
  const context = await readStateCookie();
  await clearStateCookie();

  if (!session?.accessToken) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 });
  }
  if (!context) {
    return back(origin, '/submissions/returns', 'expired');
  }

  const params = request.nextUrl.searchParams;
  if (params.get('error')) {
    return back(origin, context.returnTo, 'declined');
  }
  const code = params.get('code');
  // Constant-length compare is unnecessary here (state is single-use and
  // server-generated), but a mismatch must fail closed.
  if (!code || params.get('state') !== context.state) {
    return back(origin, context.returnTo, 'invalid');
  }

  try {
    const config = await fetchClientConfig();
    if (!config.enabled || !config.issuer || !config.client_id || !config.client_secret) {
      return back(origin, context.returnTo, 'unavailable');
    }
    const endpoints = await discover(config.issuer);
    const tokens = await exchangeCode({
      tokenEndpoint: endpoints.token_endpoint,
      code,
      codeVerifier: context.codeVerifier,
      clientId: config.client_id,
      clientSecret: config.client_secret,
      redirectUri: stepUpRedirectUri(origin),
    });
    if (!tokens.id_token) {
      return back(origin, context.returnTo, 'no_id_token');
    }
    // OIDC Core 3.1.3.7(11): a nonce was sent, so the claim must be present and
    // must match. Binds this token to this request, not merely to this client.
    if (unverifiedNonce(tokens.id_token) !== context.nonce) {
      return back(origin, context.returnTo, 'invalid');
    }

    // The risk service is the verifier: it checks the signature against the
    // issuer's JWKS, that the subject matches the signed-in user, and that
    // auth_time is recent enough to count as step-up.
    const stepUp = await fetch(
      `${apiBase()}/banks/${context.bankId}/regulatory-packages/${context.packageId}` +
        `/attestation/step-up`,
      {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${session.accessToken}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          signing_role: context.signingRole,
          id_token: tokens.id_token,
        }),
        cache: 'no-store',
      },
    );
    if (!stepUp.ok) {
      return back(origin, context.returnTo, 'rejected');
    }
    const granted = (await stepUp.json()) as {
      authorization_token: string;
      expires_at: string;
    };
    const ttl = Math.max(
      30,
      Math.floor((Date.parse(granted.expires_at) - Date.now()) / 1000),
    );
    await writeAuthorizationCookie(granted.authorization_token, ttl);
    return back(origin, context.returnTo, 'ready');
  } catch {
    // Never surface IdP or exchange internals to the browser.
    return back(origin, context.returnTo, 'failed');
  }
}
