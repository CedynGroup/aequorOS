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

  test("shows only authorized Liquidity reads", async ({ page }) => {
    await page.goto("/liquidity");
    await expect(
      page.getByRole("heading", { name: "Liquidity Cockpit" }),
    ).toBeVisible();
    const link = page.getByRole("link", { name: "Monitoring Tools" });
    await expect(link).toBeVisible();
    await expect(
      page.getByRole("link", { name: /Capital|IRR|FX|FTP/i }),
    ).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Stress" })).toHaveCount(0);
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
