import { expect, test } from "@playwright/test";
import { decode, encode } from "@auth/core/jwt";
import { spawn } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";

import { E2E_AUTH_SECRET, E2E_PASSWORD } from "./support/mint";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";

import {
  reservePort,
  waitForLoginPage,
  stopServer,
} from "./support/dashboard-server";

const EVIDENCE_DIR =
  process.env.E2E_EVIDENCE_DIR ?? path.join(E2E_TMP, "session-cookie-hygiene");

async function signIn(
  page: import("@playwright/test").Page,
  role: "admin" | "analyst" | "approver",
): Promise<void> {
  let submitted = false;
  for (let attempt = 0; attempt < 2 && !submitted; attempt += 1) {
    await page.waitForLoadState("networkidle");
    const email = page.getByLabel("Email");
    const password = page.getByLabel("Password");
    await email.fill(`e2e.${role}@aequoros.example`);
    await password.fill(E2E_PASSWORD);
    await expect(email).toHaveValue(`e2e.${role}@aequoros.example`);
    await expect(password).toHaveValue(E2E_PASSWORD);
    const callback = page
      .waitForResponse(
        (response) => response.url().includes("/api/auth/callback/credentials"),
        { timeout: 5_000 },
      )
      .catch(() => null);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    submitted = (await callback) !== null;
  }
  expect(submitted).toBe(true);
  await expect(
    page.locator('header button[aria-haspopup="menu"]'),
  ).toBeVisible();
}

async function expectIdentity(
  page: import("@playwright/test").Page,
  role: "admin" | "analyst" | "approver",
): Promise<void> {
  await page.locator('header button[aria-haspopup="menu"]').click();
  await expect(
    page.getByText(`e2e.${role}@aequoros.example`, { exact: false }),
  ).toBeVisible();
}

test.describe("session cookie hygiene", () => {
  test.use({ storageState: { cookies: [], origins: [] } });

  test.beforeAll(() => {
    mkdirSync(EVIDENCE_DIR, { recursive: true });
  });

  test("a foreign session cookie is cleared and login still succeeds", async ({
    context,
    page,
    baseURL,
  }) => {
    const foreignCookie = await encode({
      token: { sub: "foreign-session" },
      secret: "different-dashboard-secret-not-production",
      salt: "authjs.session-token",
      maxAge: 60 * 60,
    });
    const { hostname } = new URL(baseURL!);
    await context.addCookies([
      {
        name: "authjs.session-token",
        value: foreignCookie,
        domain: hostname,
        path: "/",
        httpOnly: true,
        secure: false,
        sameSite: "Lax",
      },
    ]);

    await page.goto("/");
    await expect(page).toHaveURL(/\/login\?callbackUrl=/);
    expect(
      (await context.cookies()).some(
        (cookie) => cookie.name === "authjs.session-token",
      ),
    ).toBe(false);

    await signIn(page, "analyst");
    await expectIdentity(page, "analyst");
    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "foreign-cookie-analyst.png"),
      fullPage: true,
    });
  });

  test("sign out and account switch stay on the current host", async ({
    page,
    context,
    baseURL,
  }) => {
    const foreignCallback = new URL("/", baseURL!);
    foreignCallback.hostname = "localhost";
    await page.goto(
      `/login?callbackUrl=${encodeURIComponent(foreignCallback.href)}`,
    );
    await signIn(page, "admin");
    expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin);

    const sessionCookies = (await context.cookies()).filter((c) =>
      c.name.startsWith("authjs.session-token"),
    );
    const token = await decode({
      token: sessionCookies
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((c) => c.value)
        .join(""),
      secret: E2E_AUTH_SECRET,
      salt: "authjs.session-token",
    });
    await context.addCookies([
      {
        name: "next-auth.session-token.7",
        value: "legacy-tail",
        url: baseURL!,
      },
    ]);
    const signedOut = page.waitForResponse(
      (r) =>
        r.url().endsWith("/api/auth/signout") &&
        r.request().method() === "POST",
    );
    await page.locator('header button[aria-haspopup="menu"]').click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    const signoutResponse = await signedOut;
    expect(await signoutResponse.headerValue("set-cookie")).toContain(
      "next-auth.session-token.7=;",
    );
    const revoked = await context.request.post(
      `${E2E_API_ORIGIN}/api/v1/auth/refresh`,
      { data: { refresh_token: token!.refreshToken } },
    );
    expect(revoked.status()).toBe(401);
    writeFileSync(
      path.join(EVIDENCE_DIR, "signout.json"),
      JSON.stringify(
        {
          legacyChunkExpiredInSignoutResponse: true,
          oldRefreshTokenStatus: revoked.status(),
          host: new URL(page.url()).hostname,
        },
        null,
        2,
      ),
    );
    await expect(page).toHaveURL(/\/login$/);
    expect(new URL(page.url()).hostname).toBe("127.0.0.1");
    await page.waitForLoadState("networkidle");

    await signIn(page, "approver");
    await expectIdentity(page, "approver");
    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "admin-to-approver.png"),
      fullPage: true,
    });
  });

  test("wrong credentials retain the invalid-password message", async ({
    page,
  }) => {
    await page.goto("/login");
    await page.waitForLoadState("networkidle");
    await page.getByLabel("Email").fill("e2e.analyst@aequoros.example");
    await page.getByLabel("Password").fill("wrong-password");
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.locator('form p[role="alert"]')).toContainText(
      "Invalid email or password",
    );
    await page.screenshot({
      path: path.join(EVIDENCE_DIR, "invalid-credentials.png"),
      fullPage: true,
    });
  });

  test("a stopped backend reports the service as unreachable", async ({
    browser,
  }, testInfo) => {
    testInfo.setTimeout(150_000);
    const backendPort = await reservePort();
    const dashboardPort = await reservePort();
    const origin = `http://127.0.0.1:${dashboardPort}`;
    const dashboardDir = path.resolve(__dirname, "..");
    rmSync(path.join(dashboardDir, ".next-outage"), {
      recursive: true,
      force: true,
    });
    const nextBin = require.resolve("next/dist/bin/next");
    let output = "";
    const child = spawn(
      process.execPath,
      [nextBin, "dev", "-H", "127.0.0.1", "-p", String(dashboardPort)],
      {
        cwd: dashboardDir,
        detached: true,
        stdio: ["ignore", "pipe", "pipe"],
        env: {
          ...process.env,
          NEXT_DIST_DIR: ".next-outage",
          NEXT_PUBLIC_RISK_API_BASE_URL: `http://127.0.0.1:${backendPort}/api/v1`,
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
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      await waitForLoginPage(origin, child);
      await page.goto(`${origin}/login`);
      await page.getByLabel("Email").fill("e2e.analyst@aequoros.example");
      await page.getByLabel("Password").fill(E2E_PASSWORD);
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
      await expect(page.locator('form p[role="alert"]')).toContainText(
        "Could not reach the AequorOS service",
      );
      await page.screenshot({
        path: path.join(EVIDENCE_DIR, "backend-unreachable.png"),
        fullPage: true,
      });
    } finally {
      await context.close();
      await stopServer(child);
      rmSync(path.join(dashboardDir, ".next-outage"), {
        recursive: true,
        force: true,
      });
      await testInfo.attach("development-server.log", {
        body: output,
        contentType: "text/plain",
      });
    }
  });
});
