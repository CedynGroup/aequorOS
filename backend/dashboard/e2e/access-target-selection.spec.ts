import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";

test("baseline member switches from an excluded SDI to a bank and requests class-correct authority", async ({
  browser,
  request,
}) => {
  test.setTimeout(180_000);
  // Directory-only fixture in the disposable database; no financial data.
  const fixture = (remove: boolean) =>
    execFileSync(path.join(__dirname, "../../.venv/bin/python"), [
      "-c",
      `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    if sys.argv[2] == 'remove':
        db.execute("DELETE FROM authorization_access_requests WHERE requester_user_id=(SELECT id FROM users WHERE email='e2e.viewer@aequoros.example')")
        db.execute("DELETE FROM banks WHERE id='BK-SDI00001'")
    else:
        db.execute("INSERT INTO banks SELECT 'BK-SDI00001', organization_id, 'Alpha Savings', 'Alpha', currency, jurisdiction_code, license_type, 'savings_and_loans', NULL, created_at, updated_at FROM banks WHERE id='BK-SAMP0001'")
`,
      path.join(E2E_TMP, "e2e.db"),
      remove ? "remove" : "create",
    ]);
  fixture(false);
  const context = await browser.newContext({
    storageState: path.join(E2E_TMP, "viewer.json"),
  });
  try {
    const page = await context.newPage();
    await page.goto("/basel/planning");
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    const institution = page.getByRole("combobox", {
      name: "Institution",
      exact: true,
    });
    await expect(institution).toHaveValue("BK-SDI00001");
    await expect(
      page.getByRole("button", { name: "Request access" }),
    ).toBeDisabled();
    await institution.selectOption("BK-SAMP0001");
    await page.getByRole("button", { name: "Request access" }).click();
    await page.getByRole("button", { name: "Submit request" }).click();
    await expect(page.getByRole("status")).toContainText(
      "waiting for an organization owner",
    );
    await page.goto("/liquidity");
    await expect(institution).toHaveValue("BK-SDI00001");
    await expect(
      page.getByText("Liquidity Monitoring · Confidential · View", {
        exact: true,
      }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Request access" }).click();
    await page.getByRole("button", { name: "Submit request" }).click();
    await expect(page.getByRole("status")).toContainText(
      "waiting for an organization owner",
    );
    if (process.env.E2E_EVIDENCE_DIR)
      await page.screenshot({
        path: path.join(
          process.env.E2E_EVIDENCE_DIR,
          "sdi-class-correct-request.png",
        ),
        fullPage: true,
      });
    const activeContext = await browser.newContext({
      storageState: path.join(E2E_TMP, "liquidity_viewer.json"),
    });
    try {
      const activePage = await activeContext.newPage();
      await activePage.goto("/basel/planning");
      const activeTarget = activePage.getByRole("combobox", {
        name: "Institution",
        exact: true,
      });
      await expect(activeTarget).toHaveValue("BK-SAMP0001");
      await expect(
        activePage.getByRole("button", { name: "Request access" }),
      ).toBeEnabled();
      await activeTarget.selectOption("BK-SDI00001");
      await expect(
        activePage.getByRole("button", { name: "Request access" }),
      ).toBeDisabled();
    } finally {
      await activeContext.close();
    }
    const headers = {
      Authorization: `Bearer ${await mintBackendToken("viewer")}`,
    };
    for (const [route, module_scope, sensitivity_scope] of [
      ["/ftp/scenarios", "ftp", "confidential"],
      ["/icaap", "cap", "confidential"],
      ["/irr/standardised", "irrbb", "aggregated"],
    ]) {
      const response = await request.post(
        `${E2E_API_ORIGIN}/api/v1/authorization/access-requests`,
        {
          headers,
          data: {
            route,
            institution_id: "BK-SAMP0001",
            module_scope,
            sensitivity_scope,
            permission: "view",
            reason_category: "role_change",
          },
        },
      );
      expect(response.status(), `${route}: ${await response.text()}`).toBe(201);
    }
  } finally {
    await context.close();
    fixture(true);
  }
});
