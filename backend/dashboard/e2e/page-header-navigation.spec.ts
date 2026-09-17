// The live detail test initializes the storage client but fails before any
// object-store operation. Without local storage configuration, run with:
// STORAGE_ENV=dev STORAGE_BACKEND=s3 S3_ENDPOINT=http://127.0.0.1:9
// S3_ACCESS_KEY=e2e-unused S3_SECRET_KEY=e2e-unused pnpm e2e page-header-navigation
import { expect, test } from "@playwright/test";
import { mkdirSync } from "fs";
import path from "path";
import { mintBackendToken } from "./support/mint";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.use({
  storageState: path.join(E2E_TMP, "admin.json"),
});

test("module headers rely on the tab strip instead of breadcrumbs", async ({
  page,
}) => {
  await page.goto("/data-engine");

  await expect(
    page.getByRole("heading", { name: "Data Engine" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Module sections" }),
  ).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Breadcrumb" }),
  ).toHaveCount(0);

  if (evidenceDir) {
    mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({
      path: path.join(evidenceDir, "module-header-without-breadcrumb.png"),
      fullPage: true,
    });
  }
});

test("object detail headers retain linked breadcrumbs", async ({ page, request }) => {
  // A missing source creates a real failed ingestion attempt without writing
  // canonical bank data or requiring an object-store operation.
  const headers = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const bankUrl = `${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001`;
  const mapping = await request.post(`${bankUrl}/mapping-configs`, {
    headers,
    data: {
      source_system: "EXCEL_CSV", name: "Header navigation test",
      config: {}, activate: true, reason: "Verify persisted ingestion detail navigation",
    },
  });
  expect(mapping.ok(), await mapping.text()).toBeTruthy();
  const attempt = await request.post(`${bankUrl}/ingestion-batches`, {
    headers,
    data: {
      source_system: "EXCEL_CSV", as_of_date: "2026-08-31",
      location: path.join(E2E_TMP, "missing-header-test-source.csv"),
      reason: "Verify failed ingestion detail header",
    },
  });
  expect(attempt.status(), await attempt.text()).toBe(201);
  expect((await attempt.json()).batch.status).toBe("failed");
  await page.goto("/data-engine");
  await page.getByRole("link", { name: "Detail", exact: true }).first().click();
  await expect(page).toHaveURL(/\/data-engine\/batches\/[^/]+$/);

  await expect(
    page.getByRole("heading", { name: /Ingestion batch/ }),
  ).toBeVisible();
  const header = page.getByRole("heading", { name: /Ingestion batch/ }).locator("..");
  await expect(header.locator("p").first()).toHaveText("Data Engine");
  const breadcrumb = page.getByRole("navigation", { name: "Breadcrumb" });
  await expect(breadcrumb).toBeVisible();
  await expect(
    breadcrumb.getByRole("link", { name: "Data Engine" }),
  ).toHaveAttribute("href", "/data-engine");
  await expect(
    breadcrumb.getByRole("link", { name: "Batches" }),
  ).toHaveAttribute("href", "/data-engine");

  if (evidenceDir) {
    mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({
      path: path.join(evidenceDir, "detail-header-with-breadcrumb.png"),
      fullPage: true,
    });
  }
  for (const label of ["Batches", "Data Engine"]) {
    const detailUrl = page.url();
    await breadcrumb.getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(/\/data-engine$/);
    await expect(page.getByRole("heading", { name: "Data Engine", exact: true })).toBeVisible();
    await page.goto(detailUrl);
    await expect(breadcrumb).toBeVisible();
  }

});


test("IRRBB tabs keep the module eyebrow without breadcrumbs", async ({ page }) => {
  for (const route of ["/irr", "/irr/gaps", "/irr/sensitivity", "/irr/limits"]) {
    await page.goto(route);
    const heading = page.getByRole("heading", { name: "Interest Rate Risk", exact: true });
    await expect(heading).toBeVisible();
    await expect(heading.locator("..").locator("p").first()).toHaveText("IRRBB");
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveCount(0);
    await expect(page.getByRole("navigation", { name: "Module sections" })).toBeVisible();
  }
});

for (const [route, eyebrow] of [
  ["/", "Command Center"],
  ["/liquidity", "Liquidity"],
  ["/credit", "Credit"],
  ["/fx", "FX"],
  ["/basel", "Basel Capital"],
  ["/ftp", "FTP"],
  ["/forecasting", "Forecasting"],
  ["/behavioral", "Behavioral Models"],
  ["/reports", "Reports"],
  ["/submissions/returns", "Regulatory Reporting"],
  ["/markets", "Markets"],
  ["/positions", "Positions"],
  ["/risk", "Risk & Limits"],
  ["/alerts", "Alerts"],
]) {
  test(`module header at ${route} exposes ${eyebrow} without breadcrumbs`, async ({ page }) => {
    await page.goto(route);
    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    const header = heading.locator("..");
    await expect(header.locator("p").first()).toHaveText(eyebrow);
    await expect(header.locator("p").nth(1)).toBeVisible();
    await expect(page.getByRole("navigation", { name: "Breadcrumb" })).toHaveCount(0);
  });
}
