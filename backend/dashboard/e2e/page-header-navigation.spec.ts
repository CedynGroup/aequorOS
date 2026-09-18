// The live detail test initializes the storage client but fails before any
// object-store operation. Without local storage configuration, run with:
// STORAGE_ENV=dev STORAGE_BACKEND=s3 S3_ENDPOINT=http://127.0.0.1:9
// S3_ACCESS_KEY=e2e-unused S3_SECRET_KEY=e2e-unused pnpm e2e page-header-navigation
import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "fs";
import { execFileSync } from "node:child_process";
import path from "path";
import { mintBackendToken } from "./support/mint";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.afterEach(async ({ page }, testInfo) => {
  if (evidenceDir && testInfo.status !== testInfo.expectedStatus) {
    mkdirSync(evidenceDir, { recursive: true });
    await page.screenshot({
      path: path.join(
        evidenceDir,
        `failed-${testInfo.title.replace(/[^a-z0-9]+/gi, "-")}.png`,
      ),
      fullPage: true,
    });
  }
});

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
  const topBar = page.locator("header.sticky").first();
  const topBarGround = topBar.locator(':scope > [aria-hidden="true"]');
  await expect(topBar).not.toHaveClass(/bg-surface-raised/);
  await expect(topBar).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(topBar).toHaveCSS("backdrop-filter", "none");
  await expect(topBarGround).toHaveCSS("backdrop-filter", "blur(4px)");
  for (const [theme, background] of [
    ["light", "rgba(250, 251, 252, 0.9)"],
    ["dark", "rgba(10, 15, 26, 0.9)"],
  ] as const) {
    await page.locator("html").evaluate((html, value) => {
      html.dataset.theme = value;
    }, theme);
    await expect(topBarGround).toHaveCSS("background-color", background);
  }
  await topBar
    .getByRole("button", { name: /Search/ })
    .filter({ visible: true })
    .click();
  const palette = page.getByRole("dialog");
  await expect(palette).toBeVisible();
  await expect(palette).toHaveCSS("height", `${page.viewportSize()!.height}px`);
  expect((await palette.boundingBox())?.y).toBe(0);
  await page.keyboard.press("Escape");
  await topBar.getByRole("button", { name: /^Notifications/ }).click();
  await page.getByRole("button", { name: /^Inbox/ }).click();
  await page.getByRole("button", { name: /Open full inbox/ }).click();
  const inbox = page.getByRole("dialog", { name: "Notifications" });
  await expect(inbox).toBeVisible();
  await expect(inbox).toHaveCSS("height", `${page.viewportSize()!.height}px`);
  expect((await inbox.boundingBox())?.y).toBe(0);
  await page.keyboard.press("Escape");
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
  const moduleSections = page.getByRole("navigation", {
    name: "Module sections",
  });
  await expect(moduleSections).toBeVisible();
  const tabStrip = moduleSections.locator("..");
  await expect(tabStrip).toHaveClass(/border-b/);
  await expect(tabStrip).not.toHaveClass(/bg-surface-raised/);
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
  test(`module header at ${route} exposes only ${eyebrow} and its title`, async ({
    page,
  }) => {
    await page.goto(route);
    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    const header = heading.locator("..");
    await expect(header.locator("p").first()).toHaveText(eyebrow);
    await expect(header.locator("p")).toHaveCount(1);
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

for (const [route, title] of [
  ["/institution", "Institution Profile"],
  ["/institution/parties", "Related parties"],
  ["/institution/outlets", "Outlets"],
  ["/institution/products", "Products & licences"],
  ["/institution/history", "Name history"],
  ["/institution/registers", "Board Registers"],
]) {
  test(`Institution tab at ${route} keeps the header hierarchy and alignment`, async ({
    page,
  }) => {
    await page.goto(route);
    const heading = page.getByRole("heading", {
      level: 1,
      name: title,
      exact: true,
    });
    await expect(heading).toBeVisible();
    await expect(heading.locator("..").locator("p")).toHaveText([
      "Institution Profile",
    ]);
    await expect(
      page.getByRole("navigation", { name: "Breadcrumb" }),
    ).toHaveCount(0);
    await expect(
      page.getByRole("navigation", { name: "Module sections" }),
    ).toBeVisible();
    await expectAlignedHeaderAndCards(page);
  });
}

for (const [source, links] of [
  [
    "/behavioral/nmd-duration",
    [
      ["Feeds IRRBB assumptions", "/irr"],
      ["Feeds FTP", "/ftp"],
    ],
  ],
  [
    "/behavioral/prepayment",
    [
      ["Feeds LCR", "/liquidity"],
      ["Feeds cash-flow forecast", "/liquidity/forecast"],
    ],
  ],
  ["/behavioral/deposit-stability", [["Feeds LCR outflows", "/liquidity"]]],
] as const) {
  test(`Behavioral body feed links navigate from ${source}`, async ({
    page,
  }) => {
    for (const [label, destination] of links) {
      await page.goto(source);
      const heading = page.getByRole("heading", { level: 1 });
      await expect(heading).toBeVisible();
      await expect(heading.locator("..").locator("p")).toHaveText([
        "Behavioral Models",
      ]);
      const link = page.getByRole("link", { name: label, exact: true });
      await expect(link).toBeVisible();
      const titleBox = await heading.boundingBox();
      const linkBox = await link.boundingBox();
      expect(linkBox!.y).toBeGreaterThan(titleBox!.y + titleBox!.height);
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(
            evidenceDir,
            `${source.split("/").pop()}-body-feeds.png`,
          ),
          fullPage: true,
        });
      }
      await link.click();
      await expect(page).toHaveURL(new RegExp(`${destination}$`));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    }
  });
}

test("Markets network failure keeps the error card aligned and retry recovers", async ({
  page,
}) => {
  await page.route("**/banks/*/market-data/views*", (route) =>
    route.abort("failed"),
  );
  await page.goto("/markets");
  await expect(
    page.getByText("Could not load data", { exact: true }),
  ).toBeVisible();
  await expectAlignedHeaderAndCards(page);
  if (evidenceDir) {
    await page.screenshot({
      path: path.join(evidenceDir, "markets-error-alignment.png"),
      fullPage: true,
    });
  }
  await page.unroute("**/banks/*/market-data/views*");
  const recovered = page.waitForResponse((response) =>
    response.url().includes("/market-data/views"),
  );
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  expect((await recovered).status()).toBe(200);
  await expect(
    page.getByText("Could not load data", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "Credit monitor", exact: true }),
  ).toBeVisible();
});

test("module tabs render on page ground with a hairline and active underline", async ({
  page,
}) => {
  await page.goto("/irr/gaps");
  const tabs = page.getByRole("navigation", { name: "Module sections" });
  await expect(tabs).toBeVisible();
  await expect(tabs.locator("..")).toHaveCSS(
    "background-color",
    "rgba(0, 0, 0, 0)",
  );
  await expect(tabs.locator("..")).toHaveCSS("border-bottom-width", "1px");
  const active = tabs.locator('a[href="/irr/gaps"]');
  await expect(active).toHaveCSS("border-bottom-width", "2px");
  await expect(active).not.toHaveCSS("border-bottom-color", "rgba(0, 0, 0, 0)");
  await expectAlignedHeaderAndCards(page);
  if (evidenceDir) {
    await page.screenshot({
      path: path.join(evidenceDir, "irr-tabs-wide-alignment.png"),
      fullPage: true,
    });
  }
});

test("SDI live liquidity cards align with real backend responses", async ({
  page,
}) => {
  // Change only the disposable fixture's licence class; restore it even on failure.
  const setType = (type: string) =>
    execFileSync(path.join(__dirname, "../../.venv/bin/python"), [
      "-c",
      `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    db.execute("UPDATE banks SET institution_type=? WHERE id='BK-SAMP0001'", (sys.argv[2],))
`,
      path.join(E2E_TMP, "e2e.db"),
      type,
    ]);
  setType("savings_and_loans");
  try {
    const response = page.waitForResponse((r) =>
      r.url().includes("/sdi/liquidity-position"),
    );
    await page.goto("/liquidity");
    expect((await response).status()).toBe(200);
    await expect(
      page.getByRole("heading", { name: "LMTD Table 1 prudential ratios" }),
    ).toBeVisible();
    await expectAlignedHeaderAndCards(page);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "sdi-live-liquidity-alignment.png"),
        fullPage: true,
      });
    }
  } finally {
    setType("universal_bank");
  }
});
