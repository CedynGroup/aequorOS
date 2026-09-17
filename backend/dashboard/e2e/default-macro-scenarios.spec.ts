// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const runPermissionReason =
  "Requires IRRBB · Confidential · Run. Ask your organization owner or admin to grant it.";

test.describe("default macro scenarios", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("shows system defaults and runs one on a fresh scenario library", async ({
    page,
  }) => {
    await page.goto("/irr/scenarios");

    await expect(
      page.getByRole("heading", { name: "Enterprise Stress Workbench" }),
    ).toBeVisible();
    await expect(
      page.getByText("Base consensus path", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("IRRBB parallel +200bp", { exact: true }).first(),
    ).toBeVisible();
    await expect(
      page.getByText("BoG supervisory scenario", { exact: true }).first(),
    ).toBeVisible();
    await expect(page.getByText("not runnable", { exact: true })).toBeVisible();
    await expect(page.getByLabel("Approved scenario")).not.toHaveValue("");

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "default-macro-scenario-library.png"),
        fullPage: true,
      });
    }

    await page
      .getByRole("button", { name: "Run enterprise stress", exact: true })
      .click();

    await expect(
      page.getByText("CAR — base vs stress", { exact: true }),
    ).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("No stress run selected")).toHaveCount(0);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "default-macro-scenario-results.png"),
        fullPage: true,
      });
    }
  });

  test("keeps the run control visible and explains missing authority", async ({
    page,
  }) => {
    await page.route("**/auth/me", async (route) => {
      const response = await route.fetch();
      const profile = await response.json();
      for (const institution of profile.effective_authority
        .institution_capabilities) {
        institution.capabilities = institution.capabilities.filter(
          (capability: {
            module: string;
            sensitivity: string;
            permission: string;
          }) =>
            !(
              capability.module === "irrbb" &&
              capability.sensitivity === "confidential" &&
              capability.permission === "run"
            ),
        );
      }
      await route.fulfill({ response, json: profile });
    });

    await page.goto("/irr/scenarios");
    const run = page.getByRole("button", {
      name: "Run enterprise stress",
      exact: true,
    });
    await expect(run).toBeVisible();
    await expect(run).toBeDisabled();
    await run.hover({ force: true });
    await expect(page.getByRole("tooltip")).toHaveText(runPermissionReason);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(
          evidenceDir,
          "default-macro-scenario-run-permission.png",
        ),
      });
    }
  });
});
