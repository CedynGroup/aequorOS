/**
 * OIDC authorization-code flow with PKCE (relying-party side).
 *
 * The portal is the relying party for BoG's identity provider. It does not
 * verify the ID token's signature itself: the token comes straight from the
 * token endpoint over the back channel, and the gateway API verifies it
 * against the provider's JWKS on every call (ARCHITECTURE.md "BoG staff").
 * The portal checks what a relying party must: state, nonce, issuer,
 * audience and expiry.
 */

import type { FetchLike } from "../api/client";

export type DiscoveryDocument = {
  issuer: string;
  authorization_endpoint: string;
  token_endpoint: string;
  end_session_endpoint?: string;
};

export function stripSlash(value: string): string {
  return value.replace(/\/+$/, "");
}

export async function discover(issuer: string, fetchImpl: FetchLike = fetch): Promise<DiscoveryDocument> {
  const response = await fetchImpl(`${stripSlash(issuer)}/.well-known/openid-configuration`, {
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`OIDC discovery failed: HTTP ${response.status}`);
  const doc = (await response.json()) as Partial<DiscoveryDocument>;
  if (typeof doc.authorization_endpoint !== "string" || typeof doc.token_endpoint !== "string") {
    throw new Error("OIDC discovery returned no usable endpoints.");
  }
  return {
    issuer: typeof doc.issuer === "string" ? doc.issuer : issuer,
    authorization_endpoint: doc.authorization_endpoint,
    token_endpoint: doc.token_endpoint,
    ...(typeof doc.end_session_endpoint === "string" ? { end_session_endpoint: doc.end_session_endpoint } : {}),
  };
}

export function buildAuthorizeUrl(input: {
  authorizationEndpoint: string;
  clientId: string;
  redirectUri: string;
  scope: string;
  state: string;
  nonce: string;
  codeChallenge: string;
}): URL {
  const url = new URL(input.authorizationEndpoint);
  url.searchParams.set("response_type", "code");
  url.searchParams.set("client_id", input.clientId);
  url.searchParams.set("redirect_uri", input.redirectUri);
  url.searchParams.set("scope", input.scope);
  url.searchParams.set("state", input.state);
  url.searchParams.set("nonce", input.nonce);
  url.searchParams.set("code_challenge", input.codeChallenge);
  url.searchParams.set("code_challenge_method", "S256");
  return url;
}

export async function exchangeCode(
  input: {
    tokenEndpoint: string;
    code: string;
    redirectUri: string;
    clientId: string;
    clientSecret: string | null;
    codeVerifier: string;
  },
  fetchImpl: FetchLike = fetch,
): Promise<string> {
  const form = new URLSearchParams({
    grant_type: "authorization_code",
    code: input.code,
    redirect_uri: input.redirectUri,
    client_id: input.clientId,
    code_verifier: input.codeVerifier,
  });
  if (input.clientSecret) form.set("client_secret", input.clientSecret);
  const response = await fetchImpl(input.tokenEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: form.toString(),
    cache: "no-store",
    redirect: "error",
    signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`Token exchange failed: HTTP ${response.status}`);
  const body = (await response.json()) as { id_token?: unknown };
  if (typeof body.id_token !== "string" || !body.id_token) throw new Error("The token response carried no ID token.");
  return body.id_token;
}

/** Decodes a JWT payload WITHOUT verifying it (the API is the verifier). */
export function decodeJwtPayload(jwt: string): Record<string, unknown> | null {
  const parts = jwt.split(".");
  if (parts.length !== 3 || !parts[1]) return null;
  try {
    const value = JSON.parse(Buffer.from(parts[1], "base64url").toString("utf8")) as unknown;
    return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

export type IdTokenIdentity = { subject: string; name: string; email: string; exp: number };

export type ClaimCheck = { ok: true; identity: IdTokenIdentity } | { ok: false; reason: string };

/** The relying-party checks on an ID token's claims (OIDC Core §3.1.3.7). */
export function checkIdTokenClaims(
  claims: Record<string, unknown> | null,
  expected: { issuer: string; clientId: string; nonce: string },
  now: number = Date.now(),
): ClaimCheck {
  if (!claims) return { ok: false, reason: "malformed_id_token" };
  if (typeof claims.iss !== "string" || stripSlash(claims.iss) !== stripSlash(expected.issuer)) {
    return { ok: false, reason: "issuer_mismatch" };
  }
  const aud = claims.aud;
  const audiences = Array.isArray(aud) ? aud : [aud];
  if (!audiences.includes(expected.clientId)) return { ok: false, reason: "audience_mismatch" };
  if (claims.nonce !== expected.nonce) return { ok: false, reason: "nonce_mismatch" };
  if (typeof claims.exp !== "number" || claims.exp * 1000 <= now) return { ok: false, reason: "expired" };
  if (typeof claims.sub !== "string" || !claims.sub) return { ok: false, reason: "no_subject" };
  const email = typeof claims.email === "string" ? claims.email : "";
  const name = typeof claims.name === "string" && claims.name ? claims.name : email || claims.sub;
  return { ok: true, identity: { subject: claims.sub, name, email, exp: claims.exp } };
}
