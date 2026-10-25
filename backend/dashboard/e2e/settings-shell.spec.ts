import { expect, type Page } from "@playwright/test";
import { test } from "./support/story-identities";
import { E2E_USERS } from "./support/mint";
import path from "path";
import { E2E_TMP } from "../playwright.config";

// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;

function settingsTabs(page: Page) {
  return page.getByRole("navigation", { name: "Module sections" });
}

async function openSettingsProfile(page: Page) {
  await page.goto("/settings/profile");
  await expect(
    page.getByRole("heading", { name: "Your account", exact: true }),
  ).toBeVisible();
}

test.describe("Settings shell for an account administrator", () => {
  test.use({
    storageState: path.join(E2E_TMP, "admin.json"),
    storyConfig: { roles: ["admin"], ownedTenant: true },
  });

  test("an owner reaches member administration from Settings", async ({
    page,
  }) => {
    await openSettingsProfile(page);
    const accessLink = page.getByRole("link", {
      name: "Manage members and access",
    });
    await expect(accessLink).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-profile.png"),
        fullPage: true,
      });
    }
    await page.goto("/settings");
    await expect(accessLink).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Settings", exact: true }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-general.png"),
        fullPage: true,
      });
    }
    // The link defers to the Access area's own landing, which opens Members
    // for an organization owner.
    await expect(accessLink).toHaveAttribute("href", "/access");
    await accessLink.click();
    await expect(page).toHaveURL(/\/access\/members$/);
    await expect(
      page.getByRole("heading", { name: "Members", exact: true }),
    ).toBeVisible();
  });
});

test.describe("Settings shell for an operational user", () => {
  test.use({
    storageState: path.join(E2E_TMP, "analyst.json"),
    storyConfig: { roles: ["analyst"] },
  });

  test("sidebar and avatar enter the same shell and personal appearance persists", async ({
    page,
  }) => {
    await openSettingsProfile(page);
    await expect(
      page.getByRole("link", { name: "Manage members and access" }),
    ).toHaveCount(0);
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await page
      .getByRole("button")
      .filter({ hasText: E2E_USERS.analyst.displayName! })
      .click();
    await page.getByRole("menuitem", { name: "Profile & preferences" }).click();
    await expect(page).toHaveURL(/\/settings\/profile$/);
    await expect(
      settingsTabs(page).getByRole("link", { name: "General" }),
    ).toBeVisible();

    const saved = page.waitForResponse(
      (response) =>
        response.url().endsWith("/auth/me") &&
        response.request().method() === "PATCH",
    );
    await page.getByRole("radio", { name: /^Light/ }).click();
    const response = await saved;
    expect(response.status()).toBe(200);
    expect((await response.json()).theme).toBe("light");
    await page.reload();
    await expect(page.getByRole("radio", { name: /^Light/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-appearance-light.png"),
        fullPage: true,
      });
    }
    await settingsTabs(page).getByRole("link", { name: "General" }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await expect(
      page.getByRole("link", { name: "Manage members and access" }),
    ).toHaveCount(0);
  });
});

test.describe("Settings link for a scoped Account administrator", () => {
  test.use({
    storageState: path.join(E2E_TMP, "account_admin.json"),
    storyConfig: { roles: ["account_admin"] },
  });

  test("an Account administration binding enables the Access link on both tabs", async ({
    page,
  }) => {
    await openSettingsProfile(page);
    const accessLink = page.getByRole("link", {
      name: "Manage members and access",
    });
    await expect(accessLink).toBeVisible();
    await page.goto("/settings");
    await expect(accessLink).toBeVisible();
    await expect(accessLink).toHaveAttribute("href", "/access");
  });
});

test.describe("Settings link for a scalar Account administrator", () => {
  test.use({
    storageState: path.join(E2E_TMP, "legacy_account_admin.json"),
    storyConfig: { roles: ["legacy_account_admin"] },
  });

  test("a scalar role without a binding cannot expose the Access administration link", async ({
    page,
  }) => {
    await openSettingsProfile(page);
    await expect(
      page.getByRole("link", { name: "Manage members and access" }),
    ).toHaveCount(0);
    await page.goto("/settings");
    await expect(
      page.getByRole("link", { name: "Manage members and access" }),
    ).toHaveCount(0);
    await page.goto("/access/members");
    await expect(
      page.getByText(
        "This section is available to organization owners and administrators.",
        { exact: true },
      ),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-scalar-admin-refused.png"),
        fullPage: true,
      });
    }
  });
});
