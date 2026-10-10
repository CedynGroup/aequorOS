import { test } from "@playwright/test";
import { mkdirSync } from "fs";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID } from "./support/bi";

const OUT = process.env.SHOTS_OUT ?? path.join(E2E_TMP, "subtitle-shots");
const ROUTES = ["/forecasting/reverse-stress", "/ftp/rules", "/liquidity/cfp", "/irr", "/basel"];

test.use({ storageState: path.join(E2E_TMP, "admin.json"), viewport: { width: 1440, height: 1000 } });

test("subtitle shots", async ({ page }) => {
  test.setTimeout(ROUTES.length * 120_000);
  mkdirSync(OUT, { recursive: true });
  await page.route(`**/api/v1/banks/${SAMPLE_BANK_ID}/reverse-stress/latest**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        run_id: "00000000-0000-4000-8000-000000000001",
        bank_id: SAMPLE_BANK_ID,
        reporting_period_id: "00000000-0000-4000-8000-000000000002",
        engine_version: "e2e",
        input_hash: "e2e",
        created_at: "2026-09-30T00:00:00Z",
        narrative: "Fixture frontier.",
        liquidity_axis: { breached: true, breach_multiplier: "0.74", lcr_at_breach_pct: "98.30", lcr_min_pct: "100.0" },
        capital_axis: { breached: true, breach_multiplier: "0.94", worst_cet1_at_breach_pct: "6.35", cet1_min_pct: "6.5" },
      }),
    }),
  );
  for (const route of ROUTES) {
    await page.goto(route, { waitUntil: "networkidle" }).catch(() => {});
    await page.getByText("Sample Bank Ltd").first().waitFor({ timeout: 90_000 }).catch(() => {});
    await page.waitForLoadState("networkidle").catch(() => {});
    await page.waitForTimeout(4000);
    await page.screenshot({ path: path.join(OUT, `${route.slice(1).replaceAll("/", "__")}.png`), fullPage: false });
  }
});
