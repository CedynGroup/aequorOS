"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { validateEnvironment } = require("./serve-next.cjs");
const keys = {
  NODE_ENV: "production",
  TLS_CERT_FILE: "/cert.pem",
  TLS_KEY_FILE: "/key.pem",
};

test("refuses plaintext service URLs and disabled certificate verification", () => {
  for (const name of [
    "NEXT_PUBLIC_RISK_API_BASE_URL",
    "RISK_API_INTERNAL_BASE_URL",
    "OPERATOR_API_URL",
    "OPERATOR_OIDC_ISSUER",
    "AUTH_URL",
    "CONSOLE_BASE_URL",
  ]) {
    assert.throws(
      () => validateEnvironment({ ...keys, [name]: "http://service.example" }),
      /HTTPS/,
    );
  }
  assert.throws(
    () => validateEnvironment({ ...keys, NODE_TLS_REJECT_UNAUTHORIZED: "0" }),
    /verification/,
  );
  assert.throws(
    () =>
      validateEnvironment({
        NODE_ENV: "production",
        OPERATOR_API_URL: "https://service.example",
      }),
    /TLS_CERT_FILE/,
  );
  assert.doesNotThrow(() =>
    validateEnvironment({
      ...keys,
      OPERATOR_API_URL: "https://service.example",
    }),
  );
});
