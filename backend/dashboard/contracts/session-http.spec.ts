/** Cookie clearing and runtime host configuration are HTTP contracts. */
import { expect, test } from "@playwright/test";
import { spawn } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { E2E_AUTH_SECRET } from "../e2e/support/mint";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import {
  reservePort,
  waitForLoginPage,
  stopServer,
} from "../e2e/support/dashboard-server";
const EVIDENCE_DIR =
  process.env.E2E_EVIDENCE_DIR ?? path.join(E2E_TMP, "session-cookie-hygiene");
test.use({ storageState: { cookies: [], origins: [] } });
test.beforeAll(() => mkdirSync(EVIDENCE_DIR, { recursive: true }));
test("malformed cookie families expire on protected and session endpoints", async ({
  request,
  baseURL,
}) => {
  const bases = [
    "authjs.session-token",
    "__Secure-authjs.session-token",
    "__Host-authjs.session-token",
    "next-auth.session-token",
    "__Secure-next-auth.session-token",
    "__Host-next-auth.session-token",
  ];
  const names = bases.flatMap((name) => [name, `${name}.0`, `${name}.9`]);
  const evidence = [];
  for (const endpoint of ["/settings", "/login", "/api/auth/session"]) {
    const response = await request.get(endpoint, {
      headers: {
        cookie: names.map((n) => `${n}=broken`).join("; ") + "; unrelated=keep",
      },
      maxRedirects: 0,
    });
    const headers = response
      .headersArray()
      .filter((h) => h.name.toLowerCase() === "set-cookie")
      .map((h) => h.value);
    for (const name of names)
      expect(
        headers.some(
          (h) => h.startsWith(`${name}=;`) && h.includes("Max-Age=0"),
        ),
      ).toBe(true);
    expect(headers.some((h) => h.startsWith("unrelated="))).toBe(false);
    if (endpoint === "/settings")
      expect(new URL(response.headers().location, baseURL).origin).toBe(
        new URL(baseURL!).origin,
      );
    if (endpoint.endsWith("session")) expect(await response.json()).toBe(null);
    evidence.push({
      endpoint,
      status: response.status(),
      location: response.headers().location,
      expiredCookies: headers.filter((h) => h.includes("Max-Age=0")),
      session: endpoint.endsWith("session") ? await response.json() : undefined,
    });
  }
  writeFileSync(
    path.join(EVIDENCE_DIR, "cookie-cleanup.json"),
    JSON.stringify(evidence, null, 2),
  );
});

test("conflicting AUTH_URL warns once across runtime bundles", async ({}, testInfo) => {
  testInfo.setTimeout(150_000);
  const port = await reservePort();
  const origin = `http://127.0.0.1:${port}`;
  const dashboardDir = path.resolve(__dirname, "..");
  const distDir = ".next-host-warning";
  let output = "";
  const child = spawn(
    process.execPath,
    [
      require.resolve("next/dist/bin/next"),
      "dev",
      "-H",
      "127.0.0.1",
      "-p",
      String(port),
    ],
    {
      cwd: dashboardDir,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        NEXT_DIST_DIR: distDir,
        NEXT_PUBLIC_RISK_API_BASE_URL: `${E2E_API_ORIGIN}/api/v1`,
        NEXT_FONT_GOOGLE_MOCKED_RESPONSES: path.join(
          dashboardDir,
          "e2e/support/google-font-responses.cjs",
        ),
        APP_ENV: "test",
        AUTH_URL: "http://localhost:3001",
        AUTH_SECRET: E2E_AUTH_SECRET,
        AUTH_TRUST_HOST: "true",
        SSO_INTERNAL_KEY: "",
      },
    },
  );
  child.stdout.on("data", (chunk) => {
    output += chunk.toString();
  });
  child.stderr.on("data", (chunk) => {
    output += chunk.toString();
  });
  try {
    await waitForLoginPage(origin, child);
    // Exercise middleware, auth routes, and page rendering in one server.
    for (let round = 0; round < 2; round += 1) {
      for (const endpoint of ["/api/auth/session", "/settings", "/login"]) {
        const response = await fetch(`${origin}${endpoint}`, {
          redirect: "manual",
        });
        expect(response.status).toBe(endpoint === "/settings" ? 307 : 200);
        await response.text();
      }
    }
  } finally {
    await stopServer(child);
    rmSync(path.join(dashboardDir, distDir), {
      recursive: true,
      force: true,
    });
    await testInfo.attach("development-server.log", {
      body: output,
      contentType: "text/plain",
    });
  }
  expect(output.match(/\[auth\] AUTH_URL host/g) ?? []).toHaveLength(1);
});
