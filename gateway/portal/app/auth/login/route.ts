/**
 * GET /auth/login — start the OIDC authorization-code flow with PKCE against
 * BoG's identity provider. The state, nonce and verifier are sealed into a
 * short-lived httpOnly cookie scoped to /auth; the browser never sees them.
 */

import { NextResponse, type NextRequest } from "next/server";
import { buildAuthorizeUrl, discover } from "@/lib/auth/oidc";
import { codeChallengeS256, createCodeVerifier, randomUrlSafe } from "@/lib/auth/pkce";
import { safeReturnTo } from "@/lib/auth/return-to";
import { nowSeconds, seal } from "@/lib/auth/session-crypto";
import { OAUTH_COOKIE, portalConfig, secureCookies, sessionKey } from "@/lib/server/config";

export const dynamic = "force-dynamic";

function back(req: NextRequest, error: string): NextResponse {
  return NextResponse.redirect(new URL(`/sign-in?error=${error}`, req.url), 303);
}

export async function GET(req: NextRequest) {
  const config = portalConfig();
  if (config.mock) return NextResponse.redirect(new URL("/sign-in", req.url), 303);

  let authorizationEndpoint: string;
  try {
    authorizationEndpoint = (await discover(config.oidc.issuer)).authorization_endpoint;
  } catch {
    return back(req, "idp_unreachable");
  }

  const state = randomUrlSafe(32);
  const nonce = randomUrlSafe(32);
  const verifier = createCodeVerifier();
  const redirectUri = `${config.baseUrl ?? req.nextUrl.origin}/auth/callback`;
  const returnTo = safeReturnTo(req.nextUrl.searchParams.get("next"));

  const url = buildAuthorizeUrl({
    authorizationEndpoint,
    clientId: config.oidc.clientId,
    redirectUri,
    scope: config.oidc.scope,
    state,
    nonce,
    codeChallenge: codeChallengeS256(verifier),
  });

  const res = NextResponse.redirect(url, 303);
  res.cookies.set(
    OAUTH_COOKIE,
    seal({ state, nonce, verifier, redirect_uri: redirectUri, return_to: returnTo, exp: nowSeconds() + 600 }, sessionKey(), OAUTH_COOKIE),
    { httpOnly: true, sameSite: "lax", secure: secureCookies(), path: "/auth", maxAge: 600 },
  );
  return res;
}
