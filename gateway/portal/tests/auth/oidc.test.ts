import assert from "node:assert/strict";
import test from "node:test";
import type { FetchLike } from "../../lib/api/client";
import { buildAuthorizeUrl, checkIdTokenClaims, decodeJwtPayload, discover, exchangeCode } from "../../lib/auth/oidc";

const now = Date.parse("2026-11-21T12:00:00Z");
const exp = Math.floor(now / 1000) + 600;
const expected = { issuer: "http://localhost:8200/dev/oidc", clientId: "srg-portal", nonce: "n-1" };
const good = { iss: "http://localhost:8200/dev/oidc", aud: "srg-portal", nonce: "n-1", exp, sub: "staff-1", name: "Akosua Boateng", email: "a@x", groups: ["srg-supervisors"] };

function jwt(payload: object): string {
  return `${Buffer.from('{"alg":"ES256"}').toString("base64url")}.${Buffer.from(JSON.stringify(payload)).toString("base64url")}.sig`;
}

test("the authorize URL asks for a code with PKCE S256, state and nonce", () => {
  const url = buildAuthorizeUrl({ authorizationEndpoint: "http://idp/authorize?x=1", clientId: "srg-portal", redirectUri: "http://localhost:3100/auth/callback", scope: "openid profile email", state: "st", nonce: "no", codeChallenge: "ch" });
  const p = url.searchParams;
  assert.equal(url.origin + url.pathname, "http://idp/authorize");
  assert.equal(p.get("x"), "1");
  assert.equal(p.get("response_type"), "code");
  assert.equal(p.get("client_id"), "srg-portal");
  assert.equal(p.get("redirect_uri"), "http://localhost:3100/auth/callback");
  assert.equal(p.get("code_challenge"), "ch");
  assert.equal(p.get("code_challenge_method"), "S256");
  assert.equal(p.get("state"), "st");
  assert.equal(p.get("nonce"), "no");
});

test("relying-party checks accept a good token and refuse each bad claim", () => {
  const ok = checkIdTokenClaims(good, expected, now);
  assert.deepEqual(ok, { ok: true, identity: { subject: "staff-1", name: "Akosua Boateng", email: "a@x", exp } });
  assert.equal(checkIdTokenClaims({ ...good, iss: "http://localhost:8200/dev/oidc/" }, expected, now).ok, true, "trailing slash tolerated");
  assert.equal(checkIdTokenClaims({ ...good, aud: ["other", "srg-portal"] }, expected, now).ok, true);
  const reasons: [Record<string, unknown> | null, string][] = [
    [null, "malformed_id_token"],
    [{ ...good, iss: "https://evil" }, "issuer_mismatch"],
    [{ ...good, aud: "someone-else" }, "audience_mismatch"],
    [{ ...good, nonce: "replayed" }, "nonce_mismatch"],
    [{ ...good, exp: Math.floor(now / 1000) - 1 }, "expired"],
    [{ ...good, sub: "" }, "no_subject"],
  ];
  for (const [claims, reason] of reasons) assert.deepEqual(checkIdTokenClaims(claims, expected, now), { ok: false, reason });
});

test("JWT payloads decode without verification and junk decodes to null", () => {
  assert.deepEqual(decodeJwtPayload(jwt({ sub: "x" })), { sub: "x" });
  assert.equal(decodeJwtPayload("not-a-jwt"), null);
  assert.equal(decodeJwtPayload("a.bm90IGpzb24.c"), null);
  assert.equal(decodeJwtPayload(jwt([1, 2] as unknown as object)), null);
});

test("discovery reads the endpoints and fails loudly when they are missing", async () => {
  const urls: string[] = [];
  const ok: FetchLike = async (url) => {
    urls.push(url);
    return new Response(JSON.stringify({ issuer: "http://idp", authorization_endpoint: "http://idp/a", token_endpoint: "http://idp/t" }));
  };
  const doc = await discover("http://idp/", ok);
  assert.equal(urls[0], "http://idp/.well-known/openid-configuration");
  assert.equal(doc.token_endpoint, "http://idp/t");
  await assert.rejects(discover("http://idp", async () => new Response("{}")), /no usable endpoints/);
  await assert.rejects(discover("http://idp", async () => new Response("x", { status: 503 })), /HTTP 503/);
});

test("the code exchange posts the PKCE verifier and returns the ID token", async () => {
  let body = "";
  const fetchImpl: FetchLike = async (_url, init) => {
    body = String(init?.body);
    return new Response(JSON.stringify({ id_token: "h.p.s", access_token: "ignored" }));
  };
  const token = await exchangeCode({ tokenEndpoint: "http://idp/t", code: "c-1", redirectUri: "http://localhost:3100/auth/callback", clientId: "srg-portal", clientSecret: null, codeVerifier: "v-1" }, fetchImpl);
  assert.equal(token, "h.p.s");
  const form = new URLSearchParams(body);
  assert.equal(form.get("grant_type"), "authorization_code");
  assert.equal(form.get("code_verifier"), "v-1");
  assert.equal(form.get("client_secret"), null, "public client sends no secret");
  await assert.rejects(exchangeCode({ tokenEndpoint: "t", code: "c", redirectUri: "r", clientId: "c", clientSecret: null, codeVerifier: "v" }, async () => new Response("{}")), /no ID token/);
  await assert.rejects(exchangeCode({ tokenEndpoint: "t", code: "c", redirectUri: "r", clientId: "c", clientSecret: "s", codeVerifier: "v" }, async () => new Response("", { status: 400 })), /HTTP 400/);
});
