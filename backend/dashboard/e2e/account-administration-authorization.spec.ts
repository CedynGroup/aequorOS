// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version
// control:
// E2E_CAPABILITY_ONLY=1 E2E_EVIDENCE_DIR=<path> pnpm exec playwright test e2e/account-administration-authorization.spec.ts
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("explicit Account administrator", () => {
  test.use({ storageState: path.join(E2E_TMP, "account_admin.json") });

  test("can manage authentication without gaining operational modules", async ({
    page,
  }) => {
    // Sign-in lands on the root. Account administration alone does not reach
    // the Command Center, so the guard must route to Settings — not 404.
    await page.goto("/");
    await expect(page).toHaveURL(/\/settings(?:[?#]|$)/);
    await expect(page.getByText(/404|not found/i)).toHaveCount(0);
    await expect(
      page.getByRole("heading", { name: "Authentication (SSO)" }),
    ).toBeVisible();

    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: "Authentication (SSO)" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Save connection" }),
    ).toBeVisible();
    // Both ways into Settings share one tab strip.
    const settingsTabs = page.getByRole("navigation", {
      name: "Module sections",
    });
    await expect(settingsTabs.getByRole("link", { name: "Organization" })).toBeVisible();
    await expect(
      settingsTabs.getByRole("link", { name: "Profile & preferences" }),
    ).toBeVisible();
    // Appearance moved to the personal page; the hub no longer duplicates it.
    await expect(page.getByRole("heading", { name: "Appearance" })).toHaveCount(0);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(
          evidenceDir,
          "account-admin-authentication-administration.png",
        ),
        fullPage: true,
      });
    }

    await page.goto("/data-engine/api");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Integration keys" }),
    ).toHaveCount(0);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(
          evidenceDir,
          "account-admin-no-operational-modules.png",
        ),
        fullPage: true,
      });
    }

    // When the app signs someone out on purpose — their own grant, or an
    // administrator changing their access — the sign-in page says why.
    await page.goto("/login?reason=access_changed");
    await expect(page.getByRole("status")).toContainText(
      "Your access was updated",
    );
    await page.goto("/login?reason=session_ended");
    await expect(page.getByRole("status")).toContainText(
      "Your session ended",
    );
  });
});

test.describe("legacy scalar Account administrator", () => {
  test.use({
    storageState: path.join(E2E_TMP, "legacy_account_admin.json"),
  });

  test("cannot open account administration without a binding", async ({
    page,
  }) => {
    const accountRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/auth\/sso\/(connection|access-requests)|\/integration-keys/.test(
          request.url(),
        )
      ) {
        accountRequests.push(request.url());
      }
    });

    // Since #201 every active human holds the baseline `member` sentence, so a
    // legacy scalar administrator is kept in the organization console shell
    // with personal settings — and still cannot open, or even request, any
    // account-administration surface.
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/settings(?:[?#]|$)/);
    await expect(
      page.getByRole("heading", { name: "Your account" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Authentication (SSO)" }),
    ).toHaveCount(0);
    await expect(page.getByRole("heading", { name: "Members" })).toHaveCount(0);
    await expect(
      page.getByRole("button", { name: "Save connection" }),
    ).toHaveCount(0);
    expect(accountRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "legacy-account-admin-denied.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("operational Analyst", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("can use API Push without administering integration keys", async ({
    page,
  }) => {
    const keyRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/integration-keys/.test(request.url())) {
        keyRequests.push(request.url());
      }
    });

    // An operational analyst reaches Settings through the personal tab only,
    // and the strip does not offer an Organization tab they cannot open.
    await page.goto("/settings");
    await expect(page).toHaveURL(/\/settings\/profile(?:[?#]|$)/);
    const settingsTabs = page.getByRole("navigation", {
      name: "Module sections",
    });
    await expect(
      settingsTabs.getByRole("link", { name: "Profile & preferences" }),
    ).toBeVisible();
    await expect(
      settingsTabs.getByRole("link", { name: "Organization" }),
    ).toHaveCount(0);
    // The personal page keeps the shell: institution in the header, modules
    // in the sidebar. (#188 skipped the institution load here and blanked
    // both for everyone.)
    await expect(page.getByText("Sample Bank Ltd").first()).toBeVisible();
    await expect(
      page.getByRole("navigation").getByRole("link", { name: "Liquidity" }),
    ).toBeVisible();

    await page.goto("/data-engine/api");
    await expect(page.getByRole("heading", { name: "API Push" })).toBeVisible();
    await expect(
      page.getByText("Integration keys are managed by an administrator."),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Generate key" }),
    ).toHaveCount(0);
    expect(keyRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "analyst-cannot-administer-keys.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Organization Owner", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("can administer integration keys from an authorized product page", async ({
    page,
  }) => {
    await page.goto("/data-engine/api");
    await expect(
      page.getByRole("heading", { name: "Integration keys" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Generate key" }),
    ).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(
          evidenceDir,
          "organization-owner-key-administration.png",
        ),
        fullPage: true,
      });
    }
  });
});
