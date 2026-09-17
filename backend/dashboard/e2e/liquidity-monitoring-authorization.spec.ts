// Set E2E_EVIDENCE_DIR to write screenshot evidence to that directory:
// E2E_CAPABILITY_ONLY=1 E2E_EVIDENCE_DIR=<path> pnpm exec playwright test e2e/liquidity-monitoring-authorization.spec.ts
// Keep the output outside version control; /docs/** is ignored by publication policy.
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("unbound Liquidity Monitoring user", () => {
  test.use({ storageState: path.join(E2E_TMP, "viewer.json") });

  test("hides navigation and 404s the deep link", async ({ page }) => {
    const productRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/banks\/[^/]+\/(live-summary|freshness|liquidity)/.test(request.url())
      ) {
        productRequests.push(request.url());
      }
    });
    await page.goto("/");
    await expect(
      page.getByText("No authorized institutions", { exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("navigation")).toHaveCount(0);

    await page.goto("/liquidity");
    await expect(
      page.getByRole("link", { name: "Monitoring Tools" }),
    ).toHaveCount(0);

    await page.goto("/liquidity/monitoring");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    expect(productRequests).toEqual([]);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "liquidity-monitoring-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("exactly bound Liquidity user", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("confidential-only monitoring omits unavailable metric status and names collapsed links", async ({
    page,
  }) => {
    await page.route("**/auth/me", async (route) => {
      const response = await route.fetch();
      const profile = await response.json();
      for (const institution of profile.effective_authority
        .institution_capabilities) {
        institution.capabilities = institution.capabilities.filter(
          (capability: { module: string; sensitivity: string }) =>
            capability.module !== "liq" ||
            capability.sensitivity === "confidential",
        );
      }
      await route.fulfill({ response, json: profile });
    });
    const aggregateRequests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/liquidity/dashboard")) {
        aggregateRequests.push(request.url());
      }
    });
    await page.goto("/liquidity/monitoring");
    for (const label of ["FX funding gap", "FX share of liabilities"]) {
      const card = page.locator(".card").filter({
        has: page.getByText(label, { exact: true }),
      });
      await expect(card).toBeVisible();
      await expect(card.getByText("—", { exact: true })).toBeVisible();
      await expect(card).not.toHaveCSS("box-shadow", /inset/);
    }
    expect(aggregateRequests).toEqual([]);
    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    const liquidity = page
      .getByRole("navigation")
      .getByRole("link", { name: "Liquidity", exact: true });
    await expect(liquidity).toHaveAttribute("aria-disabled", "true");
    const reason =
      "Requires Liquidity Monitoring · Aggregated · View. Ask your organization owner or admin to grant it.";
    await liquidity.hover();
    await expect(
      page.getByRole("tooltip").filter({ hasText: reason }),
    ).toBeVisible();
    await liquidity.focus();
    await expect(liquidity).toBeFocused();
    await expect(
      page.getByRole("tooltip").filter({ hasText: reason }),
    ).toBeVisible();
    await liquidity.press("Enter");
    await expect(page).toHaveURL(/\/liquidity\/monitoring$/);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "liquidity-confidential-monitoring.png"),
        fullPage: true,
      });
    }
  });

  test("shows only authorized Liquidity reads", async ({ page }) => {
    await page.goto("/liquidity");
    await expect(
      page.getByRole("heading", { name: "Liquidity Cockpit" }),
    ).toBeVisible();
    await page.getByRole("button", { name: "Collapse sidebar" }).click();
    const sidebarLiquidity = page
      .getByRole("navigation")
      .getByRole("link", { name: "Liquidity", exact: true });
    await expect(sidebarLiquidity).toBeVisible();
    await expect(sidebarLiquidity).toHaveAttribute("href", "/liquidity");
    await sidebarLiquidity.focus();
    await expect(
      page.getByRole("tooltip").filter({ hasText: /^Liquidity$/ }),
    ).toBeVisible();
    const link = page.getByRole("link", { name: "Monitoring Tools" });
    await expect(link).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Capital|IRR|FX|FTP/i }),
    ).toHaveCount(0);
    const stress = page.getByRole("link", { name: "Stress" });
    await expect(stress).toHaveAttribute("aria-disabled", "true");
    await stress.hover();
    await expect(
      page.getByRole("tooltip").filter({
        hasText:
          "Requires Risk & Limits · Confidential · View. Ask your organization owner or admin to grant it.",
      }),
    ).toBeVisible();
    await link.click();
    await expect(page).toHaveURL(/\/liquidity\/monitoring$/);
    await expect(
      page.getByRole("heading", { name: "Liquidity Monitoring Tools" }).first(),
    ).toBeVisible();
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "liquidity-monitoring-bound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("aggregated-only Liquidity user", () => {
  test.use({
    storageState: path.join(E2E_TMP, "liquidity_aggregated_viewer.json"),
  });

  test("omits confidential assessments and requests from the cockpit", async ({
    page,
  }) => {
    const confidentialRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/banks\/[^/]+\/(liquidity\/(ewis|cfp)|regulatory-runs\/|liquidity-thresholds|liquidity-haircuts)/.test(
          request.url(),
        )
      ) {
        confidentialRequests.push(request.url());
      }
    });
    const dashboardResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/liquidity/dashboard") &&
        response.status() === 200,
    );
    await page.goto("/liquidity");
    await dashboardResponse;
    await expect(
      page.getByText("Largest HQLA concentration", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("LCR headroom", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByText("Early-warning posture", { exact: true }),
    ).toHaveCount(0);
    await expect(page.getByText("CFP readiness", { exact: true })).toHaveCount(
      0,
    );
    await expect(
      page.getByText(/pp above minimum|above minimum requirement/),
    ).toHaveCount(0);
    const monitoring = page.getByRole("link", {
      name: "Monitoring Tools",
    });
    await expect(monitoring).toHaveAttribute("aria-disabled", "true");
    const tooltipId = await monitoring.getAttribute("aria-describedby");
    expect(tooltipId).toBeTruthy();
    await monitoring.hover();
    const tooltip = page.locator(`[id="${tooltipId}"]`);
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toHaveText(
      "Requires Liquidity Monitoring · Confidential · View. Ask your organization owner or admin to grant it.",
    );
    expect(confidentialRequests).toEqual([]);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "liquidity-aggregated-cockpit.png"),
        fullPage: true,
      });
    }
  });
});
