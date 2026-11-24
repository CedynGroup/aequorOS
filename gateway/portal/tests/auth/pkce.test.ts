import assert from "node:assert/strict";
import test from "node:test";
import { codeChallengeS256, createCodeVerifier, isValidCodeVerifier, randomUrlSafe } from "../../lib/auth/pkce";

test("S256 challenge matches the RFC 7636 appendix B example", () => {
  assert.equal(codeChallengeS256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM");
});

test("verifiers are valid, URL-safe and unique", () => {
  const seen = new Set<string>();
  for (let i = 0; i < 50; i += 1) {
    const v = createCodeVerifier();
    assert.ok(isValidCodeVerifier(v), v);
    assert.ok(!seen.has(v));
    seen.add(v);
  }
  assert.equal(isValidCodeVerifier("short"), false);
  assert.equal(isValidCodeVerifier("a".repeat(129)), false);
  assert.equal(isValidCodeVerifier(`${"a".repeat(43)}+`), false);
  assert.match(randomUrlSafe(32), /^[A-Za-z0-9_-]{43}$/);
});
