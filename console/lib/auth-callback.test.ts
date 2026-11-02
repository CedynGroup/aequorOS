import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { resolve } from "node:path";
import test from "node:test";
import { NextRequest } from "next/server";
import {
  decodeCookie,
  encodeCookie,
  OAUTH_TXN_COOKIE,
  SESSION_COOKIE,
  type OperatorSession,
} from "./server-auth";

// Resolve the app's existing alias in the emitted Node test build.
registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(
      specifier.startsWith("@/")
        ? `${resolve(__dirname, "..", specifier.slice(2))}.js`
        : specifier,
      context,
    );
  },
});

const envKeys = [
  "OPERATOR_OIDC_ISSUER",
  "OPERATOR_OIDC_CLIENT_ID",
  "OPERATOR_OIDC_CLIENT_SECRET",
  "OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS",
];

async function withEnvironment(limit: string, run: () => Promise<void>) {
  const saved = envKeys.map((key) => process.env[key]);
  process.env.OPERATOR_OIDC_ISSUER = "https://callback-fixture.example";
  process.env.OPERATOR_OIDC_CLIENT_ID = "fixture-client";
  process.env.OPERATOR_OIDC_CLIENT_SECRET = "fixture-secret";
  process.env.OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS = limit;
  try {
    await run();
  } finally {
    envKeys.forEach((key, index) => {
      if (saved[index] === undefined) delete process.env[key];
      else process.env[key] = saved[index];
    });
  }
}

function request() {
  const req = new NextRequest(
    "https://console.example/api/auth/callback?code=fixture-code&state=fixture-state",
  );
  req.cookies.set(
    OAUTH_TXN_COOKIE,
    encodeCookie({
      state: "fixture-state",
      nonce: "fixture-nonce",
      verifier: "fixture-verifier",
      redirect_uri: "https://console.example/api/auth/callback",
    }),
  );
  return req;
}

test("invalid session configuration redirects and clears the scoped transaction before code exchange", async () => {
  const { GET } = await import("../app/api/auth/callback/route");
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw new Error("Configuration failure must not consume the code.");
  };
  try {
    await withEnvironment("invalid", async () => {
      const response = await GET(request());
      assert.equal(response.status, 307);
      assert.equal(
        response.headers.get("location"),
        "https://console.example/login?error=oidc_not_configured",
      );
      const cookie = response.cookies.get(OAUTH_TXN_COOKIE);
      assert.equal(cookie?.path, "/api/auth");
      assert.equal(cookie?.maxAge, 0);
      assert.equal(calls, 0);
      assert.equal(response.cookies.has(SESSION_COOKIE), false);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("valid callback caps the session and clears the transaction at its original path", async () => {
  const { GET } = await import("../app/api/auth/callback/route");
  const originalFetch = globalThis.fetch;
  const issued = Math.floor(Date.now() / 1000);
  const payload = Buffer.from(
    JSON.stringify({
      email: "staff@example.test",
      nonce: "fixture-nonce",
      iat: issued,
      exp: issued + 3600,
    }),
  ).toString("base64url");
  globalThis.fetch = async (input, init) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    if (url.endsWith("/.well-known/openid-configuration")) {
      return Response.json({
        authorization_endpoint: "https://callback-fixture.example/authorize",
        token_endpoint: "https://callback-fixture.example/token",
      });
    }
    assert.equal(url, "https://callback-fixture.example/token");
    assert.equal(init?.method, "POST");
    assert.match(String(init?.body), /code_verifier=fixture-verifier/);
    return Response.json({ id_token: `header.${payload}.signature` });
  };
  try {
    await withEnvironment("900", async () => {
      const response = await GET(request());
      assert.equal(
        response.headers.get("location"),
        "https://console.example/tenants",
      );
      assert.equal(response.cookies.get(OAUTH_TXN_COOKIE)?.path, "/api/auth");
      assert.equal(response.cookies.get(OAUTH_TXN_COOKIE)?.maxAge, 0);
      const session = decodeCookie<OperatorSession>(
        response.cookies.get(SESSION_COOKIE)?.value,
      );
      assert.equal(session?.exp, issued + 900);
      assert.equal(response.cookies.get(SESSION_COOKIE)?.httpOnly, true);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});
