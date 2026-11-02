import assert from "node:assert/strict";
import test from "node:test";
import {
  applyWorkforcePolicy,
  oidcSessionExpiry,
  oidcSessionMaxAge,
} from "./workforce-oidc";

test("default policy preserves generic authorization parameters", () => {
  const url = new URL(
    "https://idp.example/authorize?state=txn&nonce=nonce&code_challenge=pkce",
  );
  const before = url.toString();
  applyWorkforcePolicy(url, "https://idp.example", false, {});
  assert.equal(url.toString(), before);
  assert.equal(oidcSessionMaxAge({}), 0);
  assert.equal(oidcSessionExpiry(4000, undefined, 0, 1000), 4000);
});

test("Workspace domain hint accompanies the configured Google login", () => {
  const url = new URL("https://accounts.google.com/authorize?state=txn");
  applyWorkforcePolicy(url, "https://accounts.google.com", false, {
    OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
  });
  assert.equal(url.searchParams.get("hd"), "workforce.example");
  assert.equal(url.searchParams.get("state"), "txn");
});

test("generic providers receive hosted domain only when explicitly configured", () => {
  const url = new URL("https://idp.example/authorize");
  applyWorkforcePolicy(url, "https://idp.example", false, {
    OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
  });
  assert.equal(url.searchParams.has("hd"), false);
  applyWorkforcePolicy(url, "https://idp.example", false, {
    OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
    OPERATOR_OIDC_REQUIRE_HOSTED_DOMAIN: "true",
  });
  assert.equal(url.searchParams.get("hd"), "workforce.example");
});

test("assurance and step-up policy request issuer evidence", () => {
  for (const env of [
    { OPERATOR_OIDC_REQUIRE_ASSURANCE: "true" },
    { OPERATOR_OIDC_STEP_UP_MAX_AGE_SECONDS: "300" },
  ]) {
    const url = new URL("https://accounts.google.com/authorize");
    applyWorkforcePolicy(url, "https://accounts.google.com", false, env);
    assert.deepEqual(JSON.parse(url.searchParams.get("claims")!), {
      id_token: {
        auth_time: { essential: true },
        amr: { essential: true },
        acr: null,
      },
    });
  }
});

test("generic reauth requests freshness; Google only returns evidence", () => {
  const url = new URL("https://idp.example/authorize");
  applyWorkforcePolicy(url, "https://idp.example", true, {});
  assert.equal(url.searchParams.get("max_age"), "0");
  assert.equal(url.searchParams.get("prompt"), "login");
  const google = new URL("https://accounts.google.com/authorize");
  applyWorkforcePolicy(google, "https://accounts.google.com", true, {});
  assert.equal(google.searchParams.has("max_age"), false);
  assert.equal(google.searchParams.has("prompt"), false);
  assert.ok(google.searchParams.has("claims"));
});

test("session cap uses original issue time and never extends token expiry", () => {
  assert.equal(
    oidcSessionMaxAge({ OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS: "900" }),
    900,
  );
  assert.equal(oidcSessionExpiry(4000, 900, 900, 1000), 1800);
  assert.equal(oidcSessionExpiry(1500, 900, 900, 1000), 1500);
  for (const iat of [undefined, true, "900", -1, 1001, 1000.5, 99]) {
    assert.equal(oidcSessionExpiry(4000, iat, 900, 1000), null);
  }
  assert.equal(oidcSessionExpiry(1000, 900, 900, 1000), null);
  for (const raw of ["bad", "-1", "43201", "1.5"]) {
    assert.throws(() =>
      oidcSessionMaxAge({ OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS: raw }),
    );
  }
});
