import { expect, test } from "@playwright/test";
import { SignJWT } from "jose";

const IMPERSONATION_SECRET = "e2e-impersonation-secret-not-production-000";

test("examiner inspection loads read-only navigation without tenant profile", async ({
  page,
}) => {
  const now = Math.floor(Date.now() / 1000);
  const token = await new SignJWT({
    typ: "impersonation",
    org: "OR-DEM00001",
    act_operator: "examiner@aequoros.com",
    session_id: "11111111-2222-4333-8444-555555555555",
    roles: ["examiner"],
  })
    .setProtectedHeader({ alg: "HS256" })
    .setIssuedAt(now)
    .setExpirationTime(now + 900)
    .sign(new TextEncoder().encode(IMPERSONATION_SECRET));

  await page.goto(`/inspect#${encodeURIComponent(token)}`);

  await expect(
    page.getByText("AequorOS staff is inspecting this account — read-only."),
  ).toBeVisible();
  await expect(page.getByText("Risk service unreachable")).toHaveCount(0);
  await expect(
    page.getByText("Liquidity", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("Settings", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /recompute/i })).toHaveCount(0);

  if (process.env.E2E_EVIDENCE_SCREENSHOT) {
    await page.screenshot({
      path: process.env.E2E_EVIDENCE_SCREENSHOT,
      fullPage: true,
    });
  }
});
