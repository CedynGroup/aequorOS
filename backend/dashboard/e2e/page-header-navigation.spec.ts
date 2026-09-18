// The live detail test initializes the storage client but fails before any
// object-store operation. Without local storage configuration, run with:
// STORAGE_ENV=dev STORAGE_BACKEND=s3 S3_ENDPOINT=http://127.0.0.1:9
// S3_ACCESS_KEY=e2e-unused S3_SECRET_KEY=e2e-unused pnpm e2e page-header-navigation
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "fs";
import path from "path";
import { mintBackendToken } from "./support/mint";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

async function expectAlignedHeaderAndCards(page: Page) {
  const heading = page.getByRole("heading", { level: 1 });
  const header = heading.locator("../../../..");
  const firstCard = page.locator("main .card").first();
  await expect(firstCard).toBeVisible();
  for (const width of [1280, 1920]) {
    await page.setViewportSize({ width, height: 1000 });
    await expect
      .poll(async () => {
        const titleBox = await heading.boundingBox();
        const headerContent = await header
          .locator(":scope > div")
          .boundingBox();
        const grid = await firstCard.evaluate((card) => {
          const parent = card.parentElement;
          const body =
            parent && getComputedStyle(parent).display === "grid"
              ? parent
              : card;
          const box = body.getBoundingClientRect();
          return { left: box.left, right: box.right };
        });
        return {
          left: Math.round((titleBox?.x ?? -1) - grid.left),
          right: Math.round(
            (headerContent ? headerContent.x + headerContent.width : -1) -
              grid.right,
          ),
        };
      })
      .toEqual({ left: 0, right: 0 });
    await expect(header).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(header).toHaveCSS("border-bottom-width", "0px");
    await expect(header).toHaveCSS("box-shadow", "none");
  }
}

test.use({
  storageState: path.join(E2E_TMP, "admin.json"),
});

test("module headers rely on the tab strip instead of breadcrumbs", async ({
  page,
}) => {
  await page.goto("/data-engine");

  const heading = page.getByRole("heading", { name: "Data Engine" });
  await expect(heading).toBeVisible();
  const pageHeader = page
    .locator("div.max-w-6xl")
    .filter({ has: heading })
    .first();
  await expect(pageHeader).toHaveClass(/px-8/);
  await expect(pageHeader).not.toHaveClass(/bg-surface-raised|border-b/);
  const titleBox = await heading.boundingBox();
  const bodyHeadingBox = await page
    .getByRole("heading", { name: "Integrations" })
    .boundingBox();
  expect(titleBox?.x).toBe(bodyHeadingBox?.x);
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

test("object detail headers retain linked breadcrumbs", async ({
  page,
  request,
}) => {
  // A missing source creates a real failed ingestion attempt without writing
  // canonical bank data or requiring an object-store operation.
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("admin")}`,
  };
  const bankUrl = `${E2E_API_ORIGIN}/api/v1/banks/BK-SAMP0001`;
  const mapping = await request.post(`${bankUrl}/mapping-configs`, {
    headers,
    data: {
      source_system: "EXCEL_CSV",
      name: "Header navigation test",
      config: {},
      activate: true,
      reason: "Verify persisted ingestion detail navigation",
    },
  });
  expect(mapping.ok(), await mapping.text()).toBeTruthy();
  const attempt = await request.post(`${bankUrl}/ingestion-batches`, {
    headers,
    data: {
      source_system: "EXCEL_CSV",
      as_of_date: "2026-08-31",
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
  const header = page
    .getByRole("heading", { name: /Ingestion batch/ })
    .locator("..");
  await expect(header.locator("p").first()).toHaveText("Data Engine");
  await expectAlignedHeaderAndCards(page);
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
    await expect(
      page.getByRole("heading", { name: "Data Engine", exact: true }),
    ).toBeVisible();
    await page.goto(detailUrl);
    await expect(breadcrumb).toBeVisible();
  }
});

test("IRRBB tabs keep the module eyebrow without breadcrumbs", async ({
  page,
}) => {
  for (const route of [
    "/irr",
    "/irr/gaps",
    "/irr/sensitivity",
    "/irr/limits",
  ]) {
    await page.goto(route);
    const heading = page.getByRole("heading", {
      name: "Interest Rate Risk",
      exact: true,
    });
    await expect(heading).toBeVisible();
    await expect(heading.locator("..").locator("p").first()).toHaveText(
      "IRRBB",
    );
    await expect(
      page.getByRole("navigation", { name: "Breadcrumb" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("navigation", { name: "Module sections" }),
    ).toBeVisible();
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
  test(`module header at ${route} exposes ${eyebrow} without breadcrumbs`, async ({
    page,
  }) => {
    await page.goto(route);
    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    const header = heading.locator("..");
    await expect(header.locator("p").first()).toHaveText(eyebrow);
    await expect(header.locator("p").nth(1)).toBeVisible();
    await expect(
      page.getByRole("navigation", { name: "Breadcrumb" }),
    ).toHaveCount(0);
  });
}

test("Credit waiting-for-data card shares the header edges at wide viewports", async ({
  page,
}) => {
  await page.goto("/credit");
  await expect(
    page.getByText("Waiting for data", { exact: true }),
  ).toBeVisible();
  await expectAlignedHeaderAndCards(page);
});

for (const route of ["/irr", "/fx"]) {
  test(`${route} header and card grid share both edges at wide viewports`, async ({
    page,
  }) => {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectAlignedHeaderAndCards(page);
  });
}

test("SDI liquidity header and card grid share both edges at wide viewports", async ({
  page,
}) => {
  await page.route("**/api/v1/banks", async (route) => {
    const response = await route.fetch();
    const payload = await response.json();
    for (const bank of payload.banks) {
      bank.institution_type_detail.institution_class = "sdi";
    }
    await route.fulfill({ response, json: payload });
  });
  await page.route("**/banks/*/sdi/liquidity-position", async (route) => {
    await route.fulfill({
      json: {
        as_of: "2026-08-31",
        ratios: [],
        reserves: [],
        maturity_ladder: [],
        readiness: [],
      },
    });
  });
  await page.goto("/liquidity");
  await expect(
    page.getByRole("heading", { name: "LMTD Table 1 prudential ratios" }),
  ).toBeVisible();
  await expectAlignedHeaderAndCards(page);
});
