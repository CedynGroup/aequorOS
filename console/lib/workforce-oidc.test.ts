import assert from "node:assert/strict";
import test from "node:test";
import {
  applyWorkforcePolicy,
  oidcSessionExpiry,
  oidcSessionMaxAge,
  usesGooglePolicy,
} from "./workforce-oidc";

test("default policy preserves generic authorization parameters", () => {
  const url = new URL(
    "https://idp.example/authorize?state=txn&nonce=nonce&code_challenge=pkce",
  );
  const before = url.toString();
  applyWorkforcePolicy(url, "https://idp.example", {});
  assert.equal(url.toString(), before);
  assert.equal(oidcSessionMaxAge("https://accounts.google.com", {}), 0);
  assert.equal(oidcSessionExpiry(4000, undefined, 0, 1000), 4000);
});

test("Workspace domain hint accompanies the configured Google login", () => {
  const url = new URL("https://accounts.google.com/authorize?state=txn");
  applyWorkforcePolicy(url, "https://accounts.google.com", {
    OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
  });
  assert.equal(url.searchParams.get("hd"), "workforce.example");
  assert.equal(url.searchParams.get("state"), "txn");
});

test("Google policy leaves generic providers unchanged even when enabled", () => {
  const url = new URL("https://idp.example/authorize?state=txn");
  const before = url.toString();
  applyWorkforcePolicy(url, "https://idp.example", {
    OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
    OPERATOR_GOOGLE_REQUIRE_2SV: "true",
  });
  assert.equal(url.toString(), before);
  assert.equal(
    oidcSessionMaxAge("https://idp.example", {
      OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS: "900",
    }),
    0,
  );
});

test("Google login requests a hosted-domain hint without optional assurance claims", () => {
  const google = new URL("https://accounts.google.com/authorize");
  applyWorkforcePolicy(google, "https://accounts.google.com", {});
  assert.equal(google.searchParams.get("hd"), "aequoros.com");
  for (const parameter of ["claims", "max_age", "prompt"]) {
    assert.equal(google.searchParams.has(parameter), false);
  }
});

test("mock issuer uses Google policy only under the undeployed allow-list", () => {
  for (const APP_ENV of ["local", "test"]) {
    const env = {
      APP_ENV,
      OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE: "true",
      OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
      OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS: "900",
    };
    const mock = new URL("http://127.0.0.1:8199/authorize");
    applyWorkforcePolicy(mock, mock.origin, env);
    assert.equal(mock.searchParams.get("hd"), "workforce.example");
    assert.equal(oidcSessionMaxAge(mock.origin, env), 900);
  }
  for (const APP_ENV of [
    undefined,
    "production",
    "staging",
    "preview",
    "TEST",
  ]) {
    assert.throws(() =>
      usesGooglePolicy("http://127.0.0.1:8199", {
        APP_ENV,
        OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE: "true",
      }),
    );
  }
});

test("session cap uses original issue time and never extends token expiry", () => {
  assert.equal(
    oidcSessionMaxAge("https://accounts.google.com", {
      OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS: "900",
    }),
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
      oidcSessionMaxAge("https://accounts.google.com", {
        OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS: raw,
      }),
    );
  }
});
