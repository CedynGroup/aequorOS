import { expect, test } from "@playwright/test";
import path from "node:path";
import { writeFileSync } from "node:fs";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";

for (const category of ["temporary_cover", "incident_break_glass"]) {
  test(`owner rejects a request with ${category} without an expiry`, async ({
    browser,
    request,
  }) => {
    const api = `${E2E_API_ORIGIN}/api/v1/authorization/access-requests`;
    const headers = {
      Authorization: `Bearer ${await mintBackendToken("viewer")}`,
    };
    const created = await request.post(api, {
      headers,
      data: {
        route: "/fx",
        institution_id: "BK-SAMP0001",
        module_scope: "fx",
        sensitivity_scope: "aggregated",
        permission: "view",
        reason_category: "role_change",
      },
    });
    expect(created.status()).toBe(201);
    const id = (await created.json()).id;
    const context = await browser.newContext({
      storageState: path.join(E2E_TMP, "admin.json"),
    });
    try {
      const page = await context.newPage();
      await page.goto("/access/members");
      const row = page.locator("li").filter({
        hasText: "E2E Viewer · Foreign Exchange",
      });
      await row.getByRole("button", { name: "Reject", exact: true }).click();
      const dialog = page.getByRole("dialog", {
        name: "Reject access request",
      });
      await dialog.getByLabel("Reason category").selectOption(category);
      await expect(dialog.locator('input[type="datetime-local"]')).toHaveCount(
        0,
      );
      const submit = dialog.getByRole("button", {
        name: "Reject request",
        exact: true,
      });
      await expect(submit).toBeEnabled();
      if (process.env.E2E_EVIDENCE_DIR) {
        await page.screenshot({
          path: path.join(
            process.env.E2E_EVIDENCE_DIR,
            `reject-${category}.png`,
          ),
          fullPage: true,
        });
      }
      const responsePromise = page.waitForResponse(
        (response) =>
          response.url().endsWith(`/${id}/reject`) &&
          response.request().method() === "POST",
      );
      await submit.click();
      const response = await responsePromise;
      expect(response.status()).toBe(200);
      const result = await response.json();
      expect(result.status).toBe("rejected");
      await expect(dialog).toBeHidden();
      if (process.env.E2E_EVIDENCE_DIR) {
        writeFileSync(
          path.join(process.env.E2E_EVIDENCE_DIR, `reject-${category}.json`),
          JSON.stringify(result, null, 2),
        );
      }
    } finally {
      await context.close();
    }
  });
}
