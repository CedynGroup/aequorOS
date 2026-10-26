// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect } from "@playwright/test";
import { test } from "./support/story-identities";
import { currentBankPeriod, expectBankStatus } from "./support/real-authority";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("default macro scenarios", () => {
  test.use({
    storageState: path.join(E2E_TMP, "analyst.json"),
    storyConfig: { roles: ["analyst"] },
  });

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
    await expect(
      page
        .getByRole("listitem")
        .filter({ hasText: "BoG supervisory scenario" })
        .getByText("not runnable", { exact: true }),
    ).toBeVisible();
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

  test.describe("read-only grant", () => {
    test.use({
      storageState: path.join(E2E_TMP, "viewer.json"),
      storyConfig: {
        roles: ["viewer"],
        bindings: [
          { bundle: "viewer", module: "irrbb", sensitivity: "all" },
          { bundle: "viewer", module: "risk", sensitivity: "confidential" },
          { bundle: "viewer", module: "ftp", sensitivity: "all" },
          { bundle: "viewer", module: "fx", sensitivity: "all" },
        ],
      },
    });
    test("read-only grants refuse execution and explain the missing authority in IRRBB, FTP and FX", async ({
      page,
    }) => {
      await page.goto("/irr/scenarios");
      const scenario = page.getByLabel("Approved scenario");
      await expect(scenario).not.toHaveValue("");
      await expectBankStatus("/enterprise-stress/runs", 403, {
        method: "POST",
        data: {
          scenario_id: await scenario.inputValue(),
          reporting_period_id: await currentBankPeriod(),
          reason: "Read-only scenario execution must be refused",
        },
      });
      const cloneControls = page.getByRole("button", {
        name: "Cloning requires the Analyst role or higher.",
        exact: true,
      });
      await expect(cloneControls).toHaveCount(9);
      for (const clone of await cloneControls.all()) {
        await expect(clone).toBeVisible();
        await expect(clone).toBeDisabled();
      }
      for (const [lens, reason] of [
        [
          "irr",
          "Requires IRRBB · Confidential · Run. Ask your organization owner or admin to grant it.",
        ],
        [
          "ftp",
          "Requires Funds Transfer Pricing · Confidential · Run. Ask your organization owner or admin to grant it.",
        ],
        [
          "fx",
          "Requires FX run permission at confidential sensitivity. An organization owner can grant it.",
        ],
      ]) {
        await page.goto(`/${lens}/scenarios`);
        const run = page.getByRole("button", {
          name: "Run enterprise stress",
          exact: true,
        });
        await expect(run).toBeVisible();
        await expect(run).toBeDisabled();
        await run.hover({ force: true });
        await expect(page.getByRole("tooltip")).toHaveText(reason);
      }

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
});
