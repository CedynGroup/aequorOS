import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

function settingsTabs(page: Page) {
  return page.getByRole("navigation", { name: "Module sections" });
}

/** Each setting lives on exactly one tab of the shared Settings shell. */
async function expectOneShell(page: Page) {
  await page.goto("/settings");
  await expect(
    settingsTabs(page).getByRole("link", { name: "General" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Data & compute", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "About", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Appearance", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Your account", exact: true }),
  ).toHaveCount(0);

  await settingsTabs(page)
    .getByRole("link", { name: "Profile & preferences" })
    .click();
  await expect(page).toHaveURL(/\/settings\/profile$/);
  await expect(
    page.getByRole("heading", { name: "Your account", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Appearance", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Data & compute", exact: true }),
  ).toHaveCount(0);
}

test.describe("Settings shell for an account administrator", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("both tabs, each setting once, and a way to the Access area", async ({
    page,
  }) => {
    await expectOneShell(page);
    const accessLink = page.getByRole("link", {
      name: "Manage members and access",
    });
    await expect(accessLink).toBeVisible();
    if (evidenceDir) {
      await expect(page.getByText("Signer ID")).toBeVisible();
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-profile.png"),
      });
    }
    await page.goto("/settings");
    await expect(accessLink).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-general.png"),
      });
    }
    await accessLink.click();
    await expect(page).toHaveURL(/\/access\/members$/);
  });
});

test.describe("Settings shell for an operational user", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("both tabs and no Access administration link", async ({ page }) => {
    await expectOneShell(page);
    await expect(
      page.getByRole("link", { name: "Manage members and access" }),
    ).toHaveCount(0);
    await page.goto("/settings");
    await expect(
      page.getByRole("link", { name: "Manage members and access" }),
    ).toHaveCount(0);
  });
});
