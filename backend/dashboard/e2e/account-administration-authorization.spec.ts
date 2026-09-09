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
    await page.goto("/settings");
    await expect(
      page.getByRole("heading", { name: "Authentication (SSO)" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Save connection" }),
    ).toBeVisible();
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

    await page.goto("/settings");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
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
