import { test, expect } from "@playwright/test";
import { decode, encode } from "@auth/core/jwt";
import { writeFileSync } from "node:fs";
import { E2E_API_ORIGIN } from "../playwright.config";
import { E2E_AUTH_SECRET, E2E_PASSWORD } from "./support/mint";

test("password session rotates and sign-out revokes its refresh family", async ({
  page,
  context,
  request,
}) => {
  await page.goto("/settings/profile");
  await expect(page).toHaveURL(/\/login/);
  await page.goto("/login");
  await page.waitForLoadState("networkidle");
  await page
    .getByLabel("Email", { exact: true })
    .fill("e2e.admin@aequoros.example");
  await page.getByLabel("Password", { exact: true }).fill(E2E_PASSWORD);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await expect(page).toHaveURL(/\/$/);
  await expect(
    page.getByRole("button", {
      name: "EA E2E Admin Administrator",
      exact: true,
    }),
  ).toBeVisible();
  // Hold a real issued session, then advance only its refresh deadline so the
  // actual running Auth.js handler performs the backend rotation immediately.
  await page.goto("about:blank");
  const original = (await context.cookies()).find(
    (c) => c.name === "authjs.session-token",
  )!;
  const before = (await decode({
    token: original.value,
    secret: E2E_AUTH_SECRET,
    salt: original.name,
  }))!;
  await context.addCookies([
    {
      ...original,
      value: await encode({
        token: { ...before, accessTokenExpires: 1 },
        secret: E2E_AUTH_SECRET,
        salt: original.name,
      }),
    },
  ]);
  const session = await context.request.get("/api/auth/session");
  expect(session.status()).toBe(200);
  expect((await session.json()).error).toBeUndefined();
  const rotatedCookie = (await context.cookies()).find(
    (c) => c.name === original.name,
  )!;
  const rotated = (await decode({
    token: rotatedCookie.value,
    secret: E2E_AUTH_SECRET,
    salt: original.name,
  }))!;
  expect(rotated.refreshToken).not.toBe(before.refreshToken);
  expect(Number(rotated.accessTokenExpires)).toBeGreaterThan(Date.now());
  const me = await request.get(`${E2E_API_ORIGIN}/api/v1/auth/me`, {
    headers: { Authorization: `Bearer ${rotated.accessToken}` },
  });
  expect(me.status()).toBe(200);
  await page.goto("/");
  await page
    .getByRole("button", { name: "EA E2E Admin Administrator", exact: true })
    .click();
  await page.getByRole("menuitem", { name: "Sign out", exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  const replay = await request.post(`${E2E_API_ORIGIN}/api/v1/auth/refresh`, {
    data: { refresh_token: rotated.refreshToken },
  });
  expect(replay.status()).toBe(401);
  await page.goto("/settings/profile");
  await expect(page).toHaveURL(/\/login/);
  if (process.env.E2E_EVIDENCE_DIR) {
    await page.screenshot({
      path: `${process.env.E2E_EVIDENCE_DIR}/signed-out.png`,
      fullPage: true,
    });
    writeFileSync(
      `${process.env.E2E_EVIDENCE_DIR}/session-lifecycle.json`,
      JSON.stringify(
        {
          sessionStatus: session.status(),
          refreshRotated: true,
          refreshedProfileStatus: me.status(),
          revokedRefreshReplayStatus: replay.status(),
          protectedRouteRedirect: page.url(),
        },
        null,
        2,
      ),
    );
  }
});
