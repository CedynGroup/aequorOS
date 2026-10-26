import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";

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
  // Wait for the visible selection underline to finish its color transition
  // before capturing the shared shell after client-side navigation.
  await expect(
    settingsTabs(page).getByRole("link", { name: "General" }),
  ).toHaveCSS("border-bottom-color", "rgba(0, 0, 0, 0)");
  await expect(
    settingsTabs(page).getByRole("link", { name: "Profile & preferences" }),
  ).not.toHaveCSS("border-bottom-color", "rgba(0, 0, 0, 0)");
}

test.use({ storageState: path.join(E2E_TMP, "analyst.json") });
test("Settings renders each section on one tab with the active underline", async ({
  page,
}) => {
  await expectOneShell(page);
  await expect(page.getByText(/^SGN-[0-9A-HJKMNP-TV-Z]{16}$/)).toBeVisible();
});
