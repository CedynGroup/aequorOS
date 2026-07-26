/**
 * Begin an SSO step-up: redirect the signer to their institution's IdP with
 * `prompt=login` so a fresh authentication is forced, not a silent session
 * replay. The correlation state (and the package being signed) lives in an
 * HttpOnly cookie, so a tampered query string cannot redirect the result at a
 * different return.
 */

import { NextResponse, type NextRequest } from 'next/server';
import { auth } from '@/auth';
import {
  backToCeremony,
  discover,
  fetchClientConfig,
  newOpaque,
  newPkcePair,
  stepUpRedirectUri,
  writeStateCookie,
} from '@/lib/attestation/stepUp';

export async function GET(request: NextRequest): Promise<NextResponse> {
  const session = await auth();
  if (!session?.accessToken) {
    return NextResponse.json({ error: 'not_authenticated' }, { status: 401 });
  }

  const params = request.nextUrl.searchParams;
  const bankId = params.get('bankId');
  const packageId = params.get('packageId');
  const signingRole = params.get('signingRole');
  const returnTo = params.get('returnTo') ?? '/submissions/returns';
  if (!bankId || !packageId || !signingRole) {
    return NextResponse.json(
      { error: 'bankId, packageId and signingRole are required' },
      { status: 400 },
    );
  }
  // Only ever bounce back inside this app: an open redirect here would be a
  // phishing vector on a signing action.
  if (!returnTo.startsWith('/')) {
    return NextResponse.json({ error: 'returnTo must be a relative path' }, { status: 400 });
  }

  // Every outcome from here lands the signer back on the ceremony: they pressed a
  // button inside a signing dialog, and a JSON error page is not an answer to
  // that. The dialog explains the marker and offers the password path instead.
  const origin = request.nextUrl.origin;
  let config;
  let endpoints;
  try {
    config = await fetchClientConfig();
    if (!config.enabled || !config.issuer || !config.client_id) {
      return backToCeremony(origin, returnTo, 'unavailable');
    }
    endpoints = await discover(config.issuer);
  } catch {
    // Discovery reaches the bank's IdP; its failure detail is theirs, not the
    // signer's, and belongs in server logs rather than a redirect.
    return backToCeremony(origin, returnTo, 'idp_unreachable');
  }

  const { verifier, challenge } = newPkcePair();
  const state = newOpaque();
  const nonce = newOpaque();
  await writeStateCookie({
    state,
    nonce,
    codeVerifier: verifier,
    bankId,
    packageId,
    signingRole,
    returnTo,
  });

  const authorize = new URL(endpoints.authorization_endpoint);
  authorize.searchParams.set('response_type', 'code');
  authorize.searchParams.set('client_id', config.client_id);
  authorize.searchParams.set('redirect_uri', stepUpRedirectUri(origin));
  authorize.searchParams.set('scope', 'openid email profile');
  authorize.searchParams.set('state', state);
  authorize.searchParams.set('nonce', nonce);
  authorize.searchParams.set('code_challenge', challenge);
  authorize.searchParams.set('code_challenge_method', 'S256');
  // The whole point of step-up: force a fresh authentication and let the IdP
  // report auth_time, which the risk service checks for freshness.
  authorize.searchParams.set('prompt', 'login');
  authorize.searchParams.set('max_age', '0');

  return NextResponse.redirect(authorize.toString());
}
