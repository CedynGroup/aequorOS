import assert from "node:assert/strict";
import { createHash, generateKeyPairSync, randomUUID, sign } from "node:crypto";
import { createServer } from "node:http";
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
} from "../../lib/server-auth";

// Resolve the app's existing alias in the emitted Node test build.
registerHooks({
  resolve(specifier, context, nextResolve) {
    return nextResolve(
      specifier.startsWith("@/")
        ? `${resolve(__dirname, "../..", specifier.slice(2))}.js`
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
  "OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE",
  "OPERATOR_OIDC_ALLOWED_DOMAIN",
  "APP_ENV",
  "NODE_ENV",
  "TLS_ALLOW_PLAINTEXT",
  "CONSOLE_BASE_URL",
];

async function withEnvironment(limit: string, run: () => Promise<void>) {
  const saved = envKeys.map((key) => process.env[key]);
  process.env.OPERATOR_OIDC_ISSUER = "https://accounts.google.com";
  process.env.OPERATOR_OIDC_CLIENT_ID = "fixture-client";
  process.env.OPERATOR_OIDC_CLIENT_SECRET = "fixture-secret";
  process.env.OPERATOR_OIDC_SESSION_MAX_AGE_SECONDS = limit;
  process.env.OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE = "false";
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
  const { GET } = await import("../../app/api/auth/callback/route");
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

test("valid callback caps the session and clears the transaction at its original path", async (t) => {
  // Freeze the clock so the fixture token's iat/exp and the route's own
  // expiry checks read the same instant on every run.
  t.mock.timers.enable({ apis: ["Date"] });
  const { GET } = await import("../../app/api/auth/callback/route");
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
    for (const issuer of [
      "https://accounts.google.com",
      "https://callback-fixture.example",
    ]) {
      await withEnvironment("900", async () => {
        process.env.OPERATOR_OIDC_ISSUER = issuer;
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
        assert.equal(
          session?.exp,
          issued + (issuer === "https://accounts.google.com" ? 900 : 3600),
        );
        assert.equal(response.cookies.get(SESSION_COOKIE)?.httpOnly, true);
      });
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("deployed console refuses mock Google policy before contacting an issuer", async () => {
  const { GET } = await import("../../app/api/auth/login/route");
  const originalFetch = globalThis.fetch;
  let calls = 0;
  globalThis.fetch = async () => {
    calls += 1;
    throw new Error("Deployed test policy must not contact the issuer.");
  };
  try {
    await withEnvironment("900", async () => {
      process.env.APP_ENV = "staging";
      process.env.OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE = "true";
      process.env.OPERATOR_OIDC_ISSUER = "http://127.0.0.1:8199";
      const result = await GET(
        new NextRequest("https://console.example/api/auth/login"),
      );
      assert.equal(
        result.headers.get("location"),
        "https://console.example/login?error=oidc_not_configured",
      );
      assert.equal(calls, 0);
      assert.equal(result.cookies.has(OAUTH_TXN_COOKIE), false);
    });
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("console completes login through an HTTP mock provider with Google policy", async (t) => {
  // The mock provider mints the fixture id token against the same frozen
  // clock the callback route validates it with.
  t.mock.timers.enable({ apis: ["Date"] });
  const { GET: login } = await import("../../app/api/auth/login/route");
  const { GET: callback } = await import("../../app/api/auth/callback/route");
  const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const codes = new Map<
    string,
    { nonce: string; challenge: string; redirect: string }
  >();
  const failures: unknown[] = [];
  let issuer = "";
  let exchanges = 0;
  let issued = 0;
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url!, issuer);
      res.setHeader("Content-Type", "application/json");
      if (url.pathname === "/.well-known/openid-configuration") {
        res.end(
          JSON.stringify({
            issuer,
            authorization_endpoint: `${issuer}/authorize`,
            token_endpoint: `${issuer}/token`,
          }),
        );
      } else if (url.pathname === "/authorize") {
        assert.equal(url.searchParams.get("hd"), "workforce.example");
        assert.equal(url.searchParams.get("claims"), null);
        assert.equal(url.searchParams.get("code_challenge_method"), "S256");
        const code = randomUUID();
        const redirect = url.searchParams.get("redirect_uri")!;
        codes.set(code, {
          nonce: url.searchParams.get("nonce")!,
          challenge: url.searchParams.get("code_challenge")!,
          redirect,
        });
        const location = new URL(redirect);
        location.searchParams.set("code", code);
        location.searchParams.set("state", url.searchParams.get("state")!);
        res.writeHead(302, { Location: location.href });
        res.end();
      } else if (url.pathname === "/token") {
        let body = "";
        for await (const chunk of req) body += String(chunk);
        const form = new URLSearchParams(body);
        const code = form.get("code")!;
        const transaction = codes.get(code);
        assert.ok(transaction);
        codes.delete(code);
        assert.equal(form.get("grant_type"), "authorization_code");
        assert.equal(form.get("redirect_uri"), transaction.redirect);
        assert.equal(form.get("client_id"), "fixture-client");
        assert.equal(form.get("client_secret"), "fixture-secret");
        assert.equal(
          createHash("sha256")
            .update(form.get("code_verifier")!)
            .digest("base64url"),
          transaction.challenge,
        );
        issued = Math.floor(Date.now() / 1000);
        const header = Buffer.from(JSON.stringify({ alg: "RS256" })).toString(
          "base64url",
        );
        const payload = Buffer.from(
          JSON.stringify({
            iss: issuer,
            aud: "fixture-client",
            sub: "110169484474386276334",
            email: "staff@workforce.example",
            email_verified: true,
            hd: "workforce.example",
            nonce: transaction.nonce,
            iat: issued,
            exp: issued + 3600,
          }),
        ).toString("base64url");
        const signature = sign(
          "RSA-SHA256",
          Buffer.from(`${header}.${payload}`),
          privateKey,
        ).toString("base64url");
        exchanges += 1;
        res.end(
          JSON.stringify({ id_token: `${header}.${payload}.${signature}` }),
        );
      } else {
        res.writeHead(404);
        res.end();
      }
    } catch (error) {
      failures.push(error);
      res.writeHead(500);
      res.end(JSON.stringify({ error: "fixture failure" }));
    }
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address();
  assert.ok(address && typeof address !== "string");
  issuer = `http://127.0.0.1:${address.port}`;
  try {
    await withEnvironment("900", async () => {
      Object.assign(process.env, {
        APP_ENV: "test",
        NODE_ENV: "test",
        TLS_ALLOW_PLAINTEXT: "1",
        CONSOLE_BASE_URL: "http://console.example",
        OPERATOR_OIDC_GOOGLE_POLICY_TEST_MODE: "true",
        OPERATOR_OIDC_ISSUER: issuer,
        OPERATOR_OIDC_ALLOWED_DOMAIN: "workforce.example",
      });
      const started = await login(
        new NextRequest("http://console.example/api/auth/login"),
      );
      const transaction = started.cookies.get(OAUTH_TXN_COOKIE);
      assert.ok(transaction?.httpOnly);
      const authorization = await fetch(started.headers.get("location")!, {
        redirect: "manual",
      });
      assert.equal(authorization.status, 302);
      const request = new NextRequest(authorization.headers.get("location")!);
      request.cookies.set(OAUTH_TXN_COOKIE, transaction.value);
      const completed = await callback(request);
      assert.equal(
        completed.headers.get("location"),
        "http://console.example/tenants",
      );
      const sessionCookie = completed.cookies.get(SESSION_COOKIE);
      assert.ok(sessionCookie?.httpOnly);
      const session = decodeCookie<OperatorSession>(sessionCookie.value);
      assert.equal(session?.email, "staff@workforce.example");
      assert.equal(session?.exp, issued + 900);
      assert.equal(completed.cookies.get(OAUTH_TXN_COOKIE)?.maxAge, 0);
      assert.equal(exchanges, 1);
      assert.equal(codes.size, 0);
      assert.deepEqual(failures, []);
    });
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  }
});
