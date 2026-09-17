import { expect, test } from "@playwright/test";
import { mkdirSync } from "fs";
import path from "path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const batchId = "11111111-2222-4333-8444-555555555555";

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

test("object detail headers retain linked breadcrumbs", async ({ page }) => {
  await page.route(
    `**/api/v1/banks/*/ingestion-batches/${batchId}/translation-failures`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ batch_id: batchId, failures: [] }),
      });
    },
  );
  await page.route(
    `**/api/v1/banks/*/ingestion-batches/${batchId}`,
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          id: batchId,
          bank_id: "BK-SAMP0001",
          source_system: "EXCEL_CSV",
          adapter_version: "e2e",
          extraction_mode: "full",
          status: "accepted",
          as_of_date: "2026-08-31",
          content_hash: null,
          mapping_config_id: null,
          records_extracted: 12,
          records_translated: 12,
          records_accepted: 12,
          records_warning: 0,
          records_error: 0,
          records_blocked: 0,
          validation_report: {},
          etl_report: null,
          started_at: "2026-09-17T09:00:00Z",
          completed_at: "2026-09-17T09:00:01Z",
          error_code: null,
          error_message: null,
          raw_artifact_path: null,
          report_artifact_path: null,
          created_at: "2026-09-17T09:00:00Z",
        }),
      });
    },
  );

  await page.goto(`/data-engine/batches/${batchId}`);

  await expect(
    page.getByRole("heading", { name: /Ingestion batch/ }),
  ).toBeVisible();
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
});
