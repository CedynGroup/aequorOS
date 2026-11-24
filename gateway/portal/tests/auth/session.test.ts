import assert from "node:assert/strict";
import test from "node:test";
import { safeReturnTo } from "../../lib/auth/return-to";
import { deriveKey, isExpired, isPortalSession, readSessionValue, seal, sessionExpiry, unseal, MAX_SESSION_SECONDS, type PortalSession } from "../../lib/auth/session-crypto";

const key = deriveKey("test-secret-that-is-long-enough-0123456789");
const now = Date.parse("2026-11-21T12:00:00Z");
const nowS = Math.floor(now / 1000);
const session: PortalSession = { mode: "oidc", subject: "s-1", name: "Kofi Owusu", email: "k@x", id_token: "a.b.c", iat: nowS, exp: nowS + 3600 };

test("sealed cookies round-trip and are not readable as plain text", () => {
  const sealed = seal(session, key, "srg_session");
  assert.match(sealed, /^v1\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
  assert.ok(!sealed.includes("Kofi") && !Buffer.from(sealed.split(".")[2] ?? "", "base64url").toString().includes("a.b.c"));
  assert.deepEqual(unseal(sealed, key, "srg_session"), session);
  assert.notEqual(seal(session, key, "srg_session"), sealed, "a fresh IV every time");
});

test("tampered, re-purposed or foreign-key cookies are rejected", () => {
  const sealed = seal(session, key, "srg_session");
  const parts = sealed.split(".");
  const data = Buffer.from(parts[2] ?? "", "base64url");
  data[0] = (data[0] ?? 0) ^ 1;
  assert.equal(unseal(`${parts[0]}.${parts[1]}.${data.toString("base64url")}`, key, "srg_session"), null);
  assert.equal(unseal(sealed, key, "srg_oauth"), null, "bound to the cookie it was sealed for");
  assert.equal(unseal(sealed, deriveKey("another-secret-that-is-long-enough-xyz"), "srg_session"), null);
  for (const bad of [undefined, "", "v1.x", "v2.a.b", "v1..", "garbage"]) assert.equal(unseal(bad, key, "srg_session"), null);
});

test("sessions are read only while valid and unexpired", () => {
  const sealed = seal(session, key, "srg_session");
  assert.deepEqual(readSessionValue(sealed, key, "srg_session", now), session);
  assert.equal(readSessionValue(sealed, key, "srg_session", now + 3601_000), null, "expired");
  assert.equal(isExpired({ exp: nowS }, now), true);
  assert.equal(isExpired({ exp: nowS + 1 }, now), false);
  const noToken = seal({ ...session, id_token: undefined }, key, "srg_session");
  assert.equal(readSessionValue(noToken, key, "srg_session", now), null, "OIDC sessions need their ID token");
  const mock = { mode: "mock", subject: "dev", name: "Esi Asante", email: "e@x", groups: ["srg-returns-policy"], iat: nowS, exp: nowS + 60 };
  assert.ok(isPortalSession(mock));
  assert.equal(isPortalSession({ ...mock, groups: undefined }), false);
  assert.equal(isPortalSession({ ...mock, mode: "admin" }), false);
});

test("session expiry follows the ID token, capped at the portal maximum", () => {
  assert.equal(sessionExpiry(nowS + 600, now), nowS + 600);
  assert.equal(sessionExpiry(nowS + 7 * 86_400, now), nowS + MAX_SESSION_SECONDS);
  assert.equal(sessionExpiry(nowS - 1, now), null);
  assert.equal(sessionExpiry(Number.NaN, now), null);
});

test("the session secret must be long enough", () => {
  assert.throws(() => deriveKey("short"), /at least 32/);
  assert.equal(deriveKey("x".repeat(32)).length, 32);
});

test("post-sign-in destinations stay on this site", () => {
  assert.equal(safeReturnTo("/submissions/SRG-26-0114806?view=full"), "/submissions/SRG-26-0114806?view=full");
  for (const bad of [null, "", "https://evil.example", "//evil.example", "/\\evil.example", "javascript:alert(1)", "/auth/callback", "/sign-in?next=/x", "/x\u0000"]) {
    assert.equal(safeReturnTo(bad), "/", String(bad));
  }
});
