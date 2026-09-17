import { expect, test } from "@playwright/test";
import { encode } from "@auth/core/jwt";
import { spawn, type ChildProcess } from "node:child_process";
import { mkdirSync, rmSync } from "node:fs";
import net from "node:net";
import path from "node:path";

import { E2E_AUTH_SECRET, E2E_PASSWORD } from "./support/mint";
import { E2E_TMP } from "../playwright.config";

const EVIDENCE_DIR = path.join(E2E_TMP, "session-cookie-hygiene");

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
        (response) =>
          response.url().includes("/api/auth/callback/credentials"),
        { timeout: 5_000 },
      )
      .catch(() => null);
    await page.getByRole("button", { name: /^Sign in/ }).click();
    submitted = (await callback) !== null;
  }
  expect(submitted).toBe(true);
  await expect(page.locator('button[aria-haspopup="menu"]')).toBeVisible();
}

async function expectIdentity(
  page: import("@playwright/test").Page,
  role: "admin" | "analyst" | "approver",
): Promise<void> {
  await page.locator('button[aria-haspopup="menu"]').click();
  await expect(
    page.getByText(`e2e.${role}@aequoros.example`, { exact: false }),
  ).toBeVisible();
}

async function reservePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen({ host: "127.0.0.1", port: 0 }, () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        server.close();
        reject(new Error("Could not reserve an IPv4 port."));
        return;
      }
      server.close((error) => (error ? reject(error) : resolve(address.port)));
    });
  });
}

async function waitForLoginPage(origin: string, child: ChildProcess) {
  const deadline = Date.now() + 120_000;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`Isolated dashboard exited with code ${child.exitCode}.`);
    }
    try {
      const response = await fetch(`${origin}/login`);
      if (response.ok) return;
    } catch {
      // The development server has not bound its port yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  throw new Error("Timed out waiting for the isolated dashboard.");
}

async function stopServer(child: ChildProcess): Promise<void> {
  if (child.exitCode !== null || !child.pid) return;
  process.kill(-child.pid, "SIGTERM");
  await Promise.race([
    new Promise<void>((resolve) => child.once("exit", () => resolve())),
    new Promise<void>((resolve) =>
      setTimeout(() => {
        if (child.exitCode === null && child.pid) {
          process.kill(-child.pid, "SIGKILL");
        }
        resolve();
      }, 5_000),
    ),
  ]);
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
    baseURL,
  }) => {
    const foreignCallback = new URL("/", baseURL!);
    foreignCallback.hostname = "localhost";
    await page.goto(`/login?callbackUrl=${encodeURIComponent(foreignCallback.href)}`);
    await signIn(page, "admin");
    expect(new URL(page.url()).origin).toBe(new URL(baseURL!).origin);

    await page.locator('button[aria-haspopup="menu"]').click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
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

  test("a stopped backend reports the service as unreachable", async ({
    browser,
  }, testInfo) => {
    testInfo.setTimeout(120_000);
    const backendPort = await reservePort();
    const dashboardPort = await reservePort();
    const origin = `http://127.0.0.1:${dashboardPort}`;
    const dashboardDir = path.resolve(__dirname, "..");
    rmSync(path.join(dashboardDir, ".next-outage"), {
      recursive: true,
      force: true,
    });
    const nextBin = require.resolve("next/dist/bin/next");
    const child = spawn(
      process.execPath,
      [nextBin, "dev", "-H", "127.0.0.1", "-p", String(dashboardPort)],
      {
        cwd: dashboardDir,
        detached: true,
        stdio: "ignore",
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
    const context = await browser.newContext();
    const page = await context.newPage();
    try {
      await waitForLoginPage(origin, child);
      await page.goto(`${origin}/login`);
      await page.getByLabel("Email").fill("e2e.analyst@aequoros.example");
      await page.getByLabel("Password").fill(E2E_PASSWORD);
      await page.getByRole("button", { name: /^Sign in/ }).click();
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
    }
  });
});
