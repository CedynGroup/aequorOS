// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect } from "@playwright/test";
import { test } from "./support/story-identities";
import { currentBankPeriod, expectBankStatus } from "./support/real-authority";
import path from "path";
import { writeFile } from "node:fs/promises";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test.describe("unbound FTP user", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [{ bundle: "viewer", module: "liq", sensitivity: "all" }],
    },
  });

  test("denies deep links without FTP requests", async ({ page }) => {
    await expectBankStatus("/ftp/dashboard", 403);
    const ftpRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\/ftp(?:\/|$)/.test(request.url())) {
        ftpRequests.push(request.url());
      }
    });

    await page.goto("/ftp");
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    await page.goto("/ftp/scenarios");
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    expect(ftpRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "ftp-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("FTP reader without run permission", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [
        { bundle: "viewer", module: "ftp", sensitivity: "aggregated" },
        { bundle: "viewer", module: "ftp", sensitivity: "confidential" },
      ],
    },
  });

  test("keeps the run action visible, disabled, and explained", async ({
    page,
  }) => {
    await expectBankStatus("/ftp/dashboard", 200);
    await expectBankStatus("/capital/dashboard", 403);
    await expectBankStatus("/ftp/run-all-scenarios", 403, {
      method: "POST",
      data: { reporting_period_id: await currentBankPeriod() },
    });
    await page.goto("/ftp");
    await expect(
      page.getByRole("heading", { name: "Transfer Curve" }),
    ).toBeVisible();
    const runButton = page.getByRole("button", {
      name: "Run FTP scenarios",
    });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeDisabled();
    const wrapper = runButton.locator("..");
    await wrapper.focus();
    await expect(wrapper).toBeFocused();
    await expect(wrapper).toHaveAccessibleDescription(
      /Requires Funds Transfer Pricing · Confidential · Run/i,
    );
    await expect(
      page.getByRole("tooltip", {
        name: /Requires Funds Transfer Pricing · Confidential · Run/i,
      }),
    ).toBeVisible();

    await page.goto("/ftp/scenarios");
    const preview = page.getByRole("button", { name: "Preview FTP what-if" });
    await expect(preview).toBeVisible();
    await expect(preview).toBeDisabled();
    // Selecting the reporting period remounts the form; focus its ready instance.
    await expect(page.getByText(/^As of \d/)).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "Base FTP scenario" }),
    ).toHaveValue("system:baseline");
    await preview.locator("..").focus();
    await expect(preview.locator("..")).toBeFocused();
    await expect(preview.locator("..")).toHaveAccessibleDescription(
      /Requires FTP · Confidential · Run/i,
    );
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "ftp-reader-disabled-run.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("FTP analyst", () => {
  test.use({
    storageState: path.join(E2E_TMP, "analyst.json"),
    storyConfig: { roles: ["analyst"] },
  });

  test("executes scenarios with effective authority", async ({ page }) => {
    const dashboardPeriodIds: string[] = [];
    page.on("request", (request) => {
      const url = new URL(request.url());
      if (/\/banks\/[^/]+\/ftp\/dashboard$/.test(url.pathname)) {
        const periodId = url.searchParams.get("reporting_period_id");
        if (periodId) dashboardPeriodIds.push(periodId);
      }
    });

    await page.goto("/ftp");
    const runButton = page.getByRole("button", {
      name: "Run FTP scenarios",
    });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeEnabled();

    const batchResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/ftp/run-all-scenarios") &&
        response.request().method() === "POST",
    );
    await runButton.click();
    const response = await batchResponse;
    expect(response.status()).toBe(201);
    const batch = await response.json();
    expect(
      batch.runs.map((run: { scenario_code: string }) => run.scenario_code),
    ).toEqual(["baseline", "rates_up_200", "funding_stress"]);
    for (const run of batch.runs) {
      expect(run.module).toBe("ftp");
      expect(run.status).toBe("succeeded");
    }
    expect(dashboardPeriodIds).toContain(batch.reporting_period_id);
    await expect(runButton).toBeEnabled();

    if (evidenceDir) {
      await writeFile(
        path.join(evidenceDir, "ftp-executed-runs.json"),
        JSON.stringify(batch, null, 2),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "ftp-analyst-enabled-run.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("FTP Reports summary permissions", () => {
  for (const sensitivity of ["aggregated", "confidential"]) {
    test.describe(`${sensitivity} authority`, () => {
      test.use({
        storageState: path.join(E2E_TMP, "viewer.json"),
        storyConfig: {
          roles: ["viewer"],
          bindings: [
            { bundle: "viewer", module: "reg", sensitivity: "all" },
            { bundle: "viewer", module: "ftp", sensitivity },
          ],
        },
      });
      test(`${sensitivity} view controls saved-analysis requests`, async ({
        page,
      }) => {
        await expectBankStatus(
          "/scenario-workbench/ftp/analyses",
          sensitivity === "aggregated" ? 200 : 403,
        );
        const summaries: string[] = [];
        page.on("request", (request) => {
          if (
            /\/scenario-workbench\/ftp\/analyses(?:\?|$)/.test(request.url())
          ) {
            summaries.push(request.url());
          }
        });
        await page.goto("/reports/analyses");
        await expect(
          page.getByRole("heading", { name: "Saved Analyses", exact: true }),
        ).toBeVisible();
        if (sensitivity === "aggregated") {
          await expect.poll(() => summaries.length).toBeGreaterThan(0);
        } else {
          expect(summaries).toEqual([]);
        }

        if (evidenceDir) {
          await page.screenshot({
            path: path.join(evidenceDir, `ftp-reports-${sensitivity}.png`),
            fullPage: true,
          });
        }
      });
    });
  }
});
