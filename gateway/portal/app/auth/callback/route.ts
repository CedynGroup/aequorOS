/**
 * GET /auth/callback — finish the OIDC code flow: check state, exchange the
 * code with the PKCE verifier, run the relying-party checks on the ID token
 * and keep it in the encrypted httpOnly session cookie. The gateway API
 * verifies the token's signature on every call.
 */

import { NextResponse, type NextRequest } from "next/server";
import { checkIdTokenClaims, decodeJwtPayload, discover, exchangeCode } from "@/lib/auth/oidc";
import { safeReturnTo } from "@/lib/auth/return-to";
import { isExpired, nowSeconds, seal, sessionExpiry, unseal, type OauthTransaction, type PortalSession } from "@/lib/auth/session-crypto";
import { OAUTH_COOKIE, SESSION_COOKIE, portalConfig, secureCookies, sessionKey } from "@/lib/server/config";

export const dynamic = "force-dynamic";

function failure(req: NextRequest, code: string): NextResponse {
  const res = NextResponse.redirect(new URL(`/sign-in?error=${code}`, req.url), 303);
  res.cookies.set(OAUTH_COOKIE, "", { path: "/auth", maxAge: 0 });
  return res;
}

export async function GET(req: NextRequest) {
  const config = portalConfig();
  if (config.mock) return NextResponse.redirect(new URL("/sign-in", req.url), 303);

  const params = req.nextUrl.searchParams;
  if (params.get("error")) return failure(req, "idp_refused");
  const code = params.get("code");
  const state = params.get("state");
  const txn = unseal<OauthTransaction>(req.cookies.get(OAUTH_COOKIE)?.value, sessionKey(), OAUTH_COOKIE);
  if (!code || !state || !txn || isExpired(txn)) return failure(req, "sign_in_expired");
  if (state !== txn.state) return failure(req, "state_mismatch");

  let idToken: string;
  try {
    const { token_endpoint } = await discover(config.oidc.issuer);
    idToken = await exchangeCode({
      tokenEndpoint: token_endpoint,
      code,
      redirectUri: txn.redirect_uri,
      clientId: config.oidc.clientId,
      clientSecret: config.oidc.clientSecret,
      codeVerifier: txn.verifier,
    });
  } catch {
    return failure(req, "token_exchange_failed");
  }

  const check = checkIdTokenClaims(decodeJwtPayload(idToken), { issuer: config.oidc.issuer, clientId: config.oidc.clientId, nonce: txn.nonce });
  if (!check.ok) return failure(req, check.reason);
  const exp = sessionExpiry(check.identity.exp);
  if (exp === null) return failure(req, "expired");

  const session: PortalSession = {
    mode: "oidc",
    subject: check.identity.subject,
    name: check.identity.name,
    email: check.identity.email,
    id_token: idToken,
    exp,
    iat: nowSeconds(),
  };
  const res = NextResponse.redirect(new URL(safeReturnTo(txn.return_to), req.url), 303);
  res.cookies.set(OAUTH_COOKIE, "", { path: "/auth", maxAge: 0 });
  res.cookies.set(SESSION_COOKIE, seal(session, sessionKey(), SESSION_COOKIE), {
    httpOnly: true,
    sameSite: "lax",
    secure: secureCookies(),
    path: "/",
    maxAge: Math.max(1, exp - nowSeconds()),
  });
  return res;
}
