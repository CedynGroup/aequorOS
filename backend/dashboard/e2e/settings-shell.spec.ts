import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { writeFileSync } from "node:fs";
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
  // Wait for the visible selection underline to finish its color transition
  // before capturing the shared shell after client-side navigation.
  await expect(
    settingsTabs(page).getByRole("link", { name: "General" }),
  ).toHaveCSS("border-bottom-color", "rgba(0, 0, 0, 0)");
  await expect(
    settingsTabs(page).getByRole("link", { name: "Profile & preferences" }),
  ).not.toHaveCSS("border-bottom-color", "rgba(0, 0, 0, 0)");
}

test.describe("Settings shell for an account administrator", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("both tabs, each setting once, and a way to the Access area", async ({
    page,
  }) => {
    await expectOneShell(page);
    const accessLink = page.getByRole("link", {
      name: "Manage integration keys and access",
    });
    await expect(accessLink).toBeVisible();
    if (evidenceDir) {
      await expect(page.getByText("Signer ID")).toBeVisible();
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-profile.png"),
        fullPage: true,
      });
    }
    await page.goto("/settings");
    await expect(accessLink).toBeVisible();
    if (evidenceDir) {
      await expect(
        page.getByText("Liquidity engine", { exact: true }),
      ).toBeVisible();
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-general.png"),
        fullPage: true,
      });
    }
    await accessLink.click();
    await expect(page).toHaveURL(/\/access\/integration-keys$/);
    await expect(
      page.getByRole("heading", { name: "Integration keys", exact: true }),
    ).toBeVisible();
    await page.getByRole("link", { name: "Members", exact: true }).click();
    await expect(page).toHaveURL(/\/access\/members$/);
    await expect(
      page.getByRole("heading", { name: "Members", exact: true }),
    ).toBeVisible();
  });
});

test.describe("Settings shell for an operational user", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("both tabs and no Access administration link", async ({ page }) => {
    await expectOneShell(page);
    await expect(
      page.getByRole("link", { name: "Manage integration keys and access" }),
    ).toHaveCount(0);
    await page.goto("/settings");
    await expect(
      page.getByRole("link", { name: "Manage integration keys and access" }),
    ).toHaveCount(0);
  });

  test("sidebar and avatar enter the same shell and personal appearance persists", async ({
    page,
  }) => {
    await page.goto("/settings/profile");
    await page.getByRole("link", { name: "Settings", exact: true }).click();
    await expect(page).toHaveURL(/\/settings$/);
    await page
      .getByRole("button", { name: "EA E2E Analyst Analyst", exact: true })
      .click();
    await page.getByRole("menuitem", { name: "Profile & preferences" }).click();
    await expect(page).toHaveURL(/\/settings\/profile$/);
    await expect(
      settingsTabs(page).getByRole("link", { name: "General" }),
    ).toBeVisible();
    await expect(page.getByText(/^SGN-[0-9A-HJKMNP-TV-Z]{16}$/)).toBeVisible();

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
      page.getByRole("heading", { name: "Appearance", exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Your account", exact: true }),
    ).toHaveCount(0);
  });
});

test.describe("Settings link for a scoped Account administrator", () => {
  test.use({ storageState: path.join(E2E_TMP, "account_admin.json") });

  test("an Account administration binding enables the Access link on both tabs", async ({
    page,
  }) => {
    await expectOneShell(page);
    const accessLink = page.getByRole("link", {
      name: "Manage integration keys and access",
    });
    await expect(accessLink).toBeVisible();
    await page.goto("/settings");
    await expect(accessLink).toBeVisible();
    const keysResponse = page.waitForResponse((response) =>
      response.url().endsWith("/integration-keys") &&
      response.request().method() === "GET",
    );
    await accessLink.click();
    await expect(page).toHaveURL(/\/access\/integration-keys$/);
    const response = await keysResponse;
    expect(response.status()).toBe(200);
    if (evidenceDir) {
      writeFileSync(
        path.join(evidenceDir, "settings-shell-scoped-admin-integration-keys-response.json"),
        JSON.stringify(
          { status: response.status(), body: await response.json() },
          null,
          2,
        ),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-scoped-admin-integration-keys.png"),
        fullPage: true,
      });
    }
    await expect(
      page.getByRole("heading", { name: "Integration keys", exact: true }),
    ).toBeVisible();
  });
});

test.describe("Settings link for a scalar Account administrator", () => {
  test.use({ storageState: path.join(E2E_TMP, "legacy_account_admin.json") });

  test("a scalar role without a binding cannot expose the Access administration link", async ({
    page,
  }) => {
    await expectOneShell(page);
    await expect(
      page.getByRole("link", { name: "Manage integration keys and access" }),
    ).toHaveCount(0);
    await page.goto("/settings");
    await expect(
      page.getByRole("link", { name: "Manage integration keys and access" }),
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

test.describe("Settings shell for a member without institution grants", () => {
  test.use({ storageState: path.join(E2E_TMP, "invite_fresh.json") });

  test("personal settings stay available while direct Access administration routes are refused", async ({
    page,
  }) => {
    await expectOneShell(page);
    await expect(
      page.getByRole("link", { name: "Manage integration keys and access" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Your account", exact: true }),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-baseline-member.png"),
        fullPage: true,
      });
    }
    for (const [anchor, route] of [
      ["members", "members"],
      ["authentication", "authentication"],
      ["integration-keys", "integration-keys"],
    ]) {
      await page.goto(`/settings#${anchor}`);
      await expect(page).toHaveURL(new RegExp(`/access/${route}$`));
      await expect(
        page.getByText(
          "This section is available to organization owners and administrators.",
          { exact: true },
        ),
      ).toBeVisible();
    }
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "settings-shell-access-refused.png"),
        fullPage: true,
      });
    }
  });
});
