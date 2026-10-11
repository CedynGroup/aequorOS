// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version
// control:
// E2E_CAPABILITY_ONLY=1 E2E_EVIDENCE_DIR=<path> pnpm exec playwright test e2e/integration-key-machine-principal.spec.ts
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("bank-scoped integration-key administration", () => {
  test.use({ storageState: path.join(E2E_TMP, "integration_admin.json") });

  test("shows the target bank, rotation warning, issuance, and revocation", async ({
    page,
  }) => {
    await page.goto("/access/integration-keys");

    await expect(
      page.getByRole("heading", { name: "Integration keys" }),
    ).toBeVisible();
    await expect(page.getByRole("radio")).toHaveCount(0);
    await expect(
      page.getByText("Analytics feed key", { exact: true }),
    ).toHaveCount(0);
    await expect(
      page.getByText(/limited to branches or regions through the API/),
    ).toHaveCount(0);
    await expect(page.getByText("Authorized institution")).toBeVisible();
    await expect(page.getByText("BK-SAMP0001", { exact: false })).toBeVisible();
    await expect(
      page.getByText("Unscoped — rotate. This legacy key cannot push data."),
    ).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "integration-key-target-and-rotation.png"),
        fullPage: true,
      });
    }

    await page
      .getByRole("textbox", { name: "Key label" })
      .fill("Reviewer evidence feed");
    await page.getByRole("button", { name: "Generate data push key" }).click();

    await expect(
      page.getByText(/Data push key generated — copy it now/),
    ).toBeVisible();
    await expect(page.getByText("Authorized institution:")).toBeVisible();
    await expect(page.getByText("Reviewer evidence feed")).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "integration-key-issued-for-bank.png"),
        fullPage: true,
      });
    }

    const issuedRow = page
      .getByRole("listitem")
      .filter({ hasText: "Reviewer evidence feed" });
    page.once("dialog", (dialog) =>
      dialog.accept("Reviewer evidence rotation proof"),
    );
    await issuedRow.getByRole("button", { name: "Revoke" }).click();
    await expect(issuedRow.getByText("Revoked")).toBeVisible();

    if (evidenceDir) {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({
        path: path.join(evidenceDir, "integration-key-revoked-with-bank.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("API Push credential guidance", () => {
  test.use({ storageState: path.join(E2E_TMP, "integration_admin.json") });

  test("offers bank-scoped data push guidance without reader credentials", async ({
    page,
  }) => {
    await page.goto("/data-engine/api");
    await expect(
      page.getByRole("heading", { name: "API Push", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/cannot read analytics or access a sibling bank/),
    ).toBeVisible();
    await expect(page.getByText(/analytics feed key/i)).toHaveCount(0);
    await page
      .getByRole("link", { name: "Access → Integration keys", exact: true })
      .click();
    await expect(
      page.getByRole("button", { name: "Generate data push key" }),
    ).toBeVisible();
    await expect(page.getByRole("radio")).toHaveCount(0);
  });
});
