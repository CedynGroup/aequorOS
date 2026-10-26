import { expect, test } from "@playwright/test";

import path from "path";

import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";

import { mintBackendToken } from "./support/mint";

const API = `${E2E_API_ORIGIN}/api/v1`;

const evidence = process.env.E2E_EVIDENCE_DIR;

test("Settings loads institutions and grant reason picker requires expiry", async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000);
  const context = await browser.newContext({
    storageState: path.join(E2E_TMP, "admin.json"),
  });
  const page = await context.newPage();
  await page.goto("/settings");
  await expect(
    page.getByText("Sample Bank Ltd", { exact: true }).first(),
  ).toBeVisible();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "owner-settings-institution.png"),
      fullPage: true,
    });
  await page.goto("/settings/profile");
  await expect(
    page.getByRole("navigation", { name: /breadcrumb/i }),
  ).toHaveCount(0);
  await page.goto("/icaap/settings/workflow");
  await expect(
    page.getByRole("heading", { name: "ICAAP review chain" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: /breadcrumb/i }),
  ).toHaveCount(0);
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "icaap-workflow-no-breadcrumb.png"),
      fullPage: true,
    });
  const pending = await request.post(`${API}/authorization/access-requests`, {
    headers: {
      Authorization: `Bearer ${await mintBackendToken("access_extra_member")}`,
    },
    data: {
      route: "/fx",
      institution_id: "BK-SAMP0001",
      module_scope: "fx",
      sensitivity_scope: "aggregated",
      permission: "view",
      reason_category: "role_change",
    },
  });
  expect(pending.status()).toBe(201);
  await page.goto("/access/members");
  await page.getByRole("button", { name: "Review request" }).click();
  await page.getByRole("button", { name: "Review grant" }).click();
  await page.getByRole("button", { name: "Grant access" }).click();
  await expect(page.getByText("Grant created", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Add another grant" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Role bundle").selectOption("viewer");
  await dialog.getByLabel("Institution coverage").selectOption("BK-SAMP0001");
  await dialog.getByLabel("Module").selectOption("liq");
  await dialog.getByLabel("Sensitivity").selectOption("confidential");
  await expect(
    dialog.getByLabel("Reason category").locator("option"),
  ).toHaveCount(7);
  await dialog.getByLabel("Reason category").selectOption("temporary_cover");
  await expect(dialog.locator('input[type="datetime-local"]')).not.toHaveValue(
    "",
  );
  await dialog.locator('input[type="datetime-local"]').fill("");
  await expect(dialog.locator('button[type="submit"]')).toBeDisabled();
  await dialog
    .getByLabel("Reason category")
    .selectOption("incident_break_glass");
  await expect(dialog.locator('input[type="datetime-local"]')).not.toHaveValue(
    "",
  );
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "grant-reason-expiry.png"),
      fullPage: true,
    });
  await dialog.getByRole("button", { name: "Review grant" }).click();
  await dialog.getByRole("button", { name: "Grant access" }).click();
  await expect(
    dialog.getByText("Grant created", { exact: true }),
  ).toBeVisible();
  if (evidence)
    await page.screenshot({
      path: path.join(evidence, "additional-grant-after-request.png"),
      fullPage: true,
    });
  await context.close();
});
