// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test } from "@playwright/test";
import path from "path";
import { writeFile } from "node:fs/promises";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test.describe("unbound FTP user", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("hides navigation and 404s deep links without FTP requests", async ({
    page,
  }) => {
    const ftpRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\/ftp(?:\/|$)/.test(request.url())) {
        ftpRequests.push(request.url());
      }
    });

    await page.goto("/ftp");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    await page.goto("/ftp/scenarios");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
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
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("keeps the run action visible, disabled, and explained", async ({
    page,
  }) => {
    await page.route("**/auth/me", async (route) => {
      const response = await route.fetch();
      const profile = await response.json();
      profile.effective_authority.organization_capabilities = [];
      for (const institution of profile.effective_authority
        .institution_capabilities) {
        institution.capabilities = institution.capabilities.filter(
          (capability: {
            module: string;
            sensitivity: string;
            permission: string;
          }) =>
            capability.module === "ftp" &&
            capability.permission === "view" &&
            ["aggregated", "confidential"].includes(capability.sensitivity),
        );
      }
      await route.fulfill({ response, json: profile });
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
    await page.getByRole("button", { name: "Scenarios & run", exact: true }).click();
    const enterpriseRun = page.getByRole("button", { name: "Run enterprise stress" });
    await expect(enterpriseRun).toBeVisible();
    await expect(enterpriseRun).toBeDisabled();
    await expect(enterpriseRun.locator("..")).toHaveAccessibleDescription(
      /Requires Funds Transfer Pricing · Confidential · Run/i,
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
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

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
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  for (const sensitivity of ["aggregated", "confidential"]) {
    test(`${sensitivity} view controls saved-analysis requests`, async ({
      page,
    }) => {
      let authorityApplied = false;
      await page.route("**/auth/me", async (route) => {
        const response = await route.fetch();
        const profile = await response.json();
        profile.effective_authority.organization_capabilities = [];
        for (const institution of profile.effective_authority
          .institution_capabilities) {
          institution.capabilities = institution.capabilities.filter(
            (capability: {
              module: string;
              sensitivity: string;
              permission: string;
            }) =>
              capability.permission === "view" &&
              (capability.module === "reg" ||
                (capability.module === "ftp" &&
                  capability.sensitivity === sensitivity)),
          );
        }
        await route.fulfill({ response, json: profile });
        authorityApplied = true;
      });

      const summaries: string[] = [];
      page.on("request", (request) => {
        if (/\/scenario-workbench\/ftp\/analyses(?:\?|$)/.test(request.url())) {
          summaries.push(request.url());
        }
      });
      await page.goto("/reports/analyses");
      await expect(
        page.getByRole("heading", { name: "Saved Analyses", exact: true }),
      ).toBeVisible();
      await expect.poll(() => authorityApplied).toBe(true);
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
  }
});
