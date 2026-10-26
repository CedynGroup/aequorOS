import { expect, test, type Page } from "@playwright/test";
import { mkdirSync } from "fs";
import path from "path";
import { E2E_TMP } from "../playwright.config";

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

/**
 * A CSS colour resolved to its sRGB pixel. Tailwind renders opacity modifiers
 * through `color-mix(in oklab, …)`, so the computed value serialises as
 * `oklab(…)`; painting both sides through the same canvas compares the colour
 * itself rather than its notation.
 */
async function srgbPixel(page: Page, color: string): Promise<number[]> {
  return page.evaluate((value) => {
    const context = document.createElement("canvas").getContext("2d")!;
    context.fillStyle = value;
    context.fillRect(0, 0, 1, 1);
    return Array.from(context.getImageData(0, 0, 1, 1).data);
  }, color);
}

import { expectAlignedHeaderAndCards } from "./support/layout";

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
  await expect(topBar).toHaveCSS("height", "64px");
  await expect(topBar).toHaveCSS("position", "sticky");
  await expect(topBar).toHaveCSS("top", "0px");
  await expect(topBar).toHaveCSS("z-index", "30");
  await expect(topBar).toHaveCSS("border-bottom-width", "1px");
  for (const [theme, background] of [
    ["light", "rgba(250, 251, 252, 0.9)"],
    ["dark", "rgba(10, 15, 26, 0.9)"],
  ] as const) {
    const themeSwitch = page.getByRole("button", {
      name: `Switch to ${theme} theme`,
    });
    if (await themeSwitch.count()) await themeSwitch.click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
    const expected = await srgbPixel(page, background);
    await expect
      .poll(async () =>
        srgbPixel(
          page,
          await topBarGround.evaluate(
            (element) => getComputedStyle(element).backgroundColor,
          ),
        ),
      )
      .toEqual(expected);
    if (evidenceDir) {
      mkdirSync(evidenceDir, { recursive: true });
      await page.screenshot({
        path: path.join(evidenceDir, `top-bar-${theme}.png`),
        fullPage: true,
      });
    }
  }
  await topBar
    .getByRole("button", { name: /Search/ })
    .filter({ visible: true })
    .click();
  const palette = page.getByRole("dialog");
  await expect(palette).toBeVisible();
  await expect(palette).toHaveCSS("height", `${page.viewportSize()!.height}px`);
  expect((await palette.boundingBox())?.y).toBe(0);
  if (evidenceDir) {
    await page.screenshot({
      path: path.join(evidenceDir, "command-palette-viewport.png"),
    });
  }
  await page.keyboard.press("Escape");
  await topBar.getByRole("button", { name: /^Notifications/ }).click();
  await page.getByRole("button", { name: /^Inbox/ }).click();
  await page.getByRole("button", { name: /Open full inbox/ }).click();
  const inbox = page.getByRole("dialog", { name: "Notifications" });
  await expect(inbox).toBeVisible();
  await expect(inbox).toHaveCSS("height", `${page.viewportSize()!.height}px`);
  expect((await inbox.boundingBox())?.y).toBe(0);
  if (evidenceDir) {
    await page.screenshot({
      path: path.join(evidenceDir, "notification-inbox-viewport.png"),
    });
  }
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

test("IRRBB tabs keep the module eyebrow without breadcrumbs", async ({
  page,
}) => {
  for (const route of [
    "/irr",
    "/irr/gaps",
    "/irr/sensitivity",
    "/irr/limits",
  ]) {
    await page.goto(
      /^(\/irr|\/liquidity)(\/|$)/.test(route) ? `${route}?preparing=1` : route,
    );
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
    await page.goto(
      /^(\/irr|\/liquidity)(\/|$)/.test(route) ? `${route}?preparing=1` : route,
    );
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

test("Credit cards share the header edges at wide viewports", async ({
  page,
}) => {
  await page.goto("/credit");
  await expectAlignedHeaderAndCards(page);
});

for (const route of ["/irr", "/fx"]) {
  test(`${route} header and card grid share both edges at wide viewports`, async ({
    page,
  }) => {
    await page.goto(
      /^(\/irr|\/liquidity)(\/|$)/.test(route) ? `${route}?preparing=1` : route,
    );
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectAlignedHeaderAndCards(page);
  });
}

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
    await page.goto(
      /^(\/irr|\/liquidity)(\/|$)/.test(route) ? `${route}?preparing=1` : route,
    );
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

test("module tabs render on page ground with a hairline and active underline", async ({
  page,
}) => {
  await page.goto("/irr/gaps?preparing=1");
  const tabs = page.getByRole("navigation", { name: "Module sections" });
  await expect(tabs).toBeVisible();
  await expect(tabs.locator("..")).toHaveCSS(
    "background-color",
    "rgba(0, 0, 0, 0)",
  );
  await expect(tabs.locator("..")).toHaveCSS("border-bottom-width", "1px");
  const active = tabs.locator('a[href="/irr/gaps?preparing=1"]');
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
  test(`Behavioral feed links stay below the heading at ${source}`, async ({
    page,
  }) => {
    for (const [label] of links) {
      // The document can load while Next is still recompiling Auth.js. Resolve
      // the session before spending the presentation assertion budget.
      const sessionReady = page.waitForResponse("**/api/auth/session");
      await page.goto(source);
      expect((await sessionReady).status()).toBe(200);
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
    }
  });
}

test("Markets aligns its error card when its data request fails", async ({
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
});
