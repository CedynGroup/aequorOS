import { expect } from "@playwright/test";
import { test } from "./support/story-identities";
import { createFailedIngestion } from "./support/ingestion-attempt";
import { mkdirSync } from "node:fs";
import path from "node:path";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
test.use({
  storageState: path.join(E2E_TMP, "analyst.json"),
  storyConfig: { roles: ["analyst"] },
});

test.describe("failed source detail", () => {
  test.use({ storyConfig: { ownedTenant: true, roles: ["admin", "analyst"] } });
  test("breadcrumbs return from the persisted failed ingestion to its batch list", async ({
    page,
    request,
    storyIdentity,
  }) => {
    const batchId = await createFailedIngestion(request, storyIdentity.bankId);
    await page.goto("/data-engine");
    await page.locator(`a[href="/data-engine/batches/${batchId}"]`).click();
    await expect(page).toHaveURL(/\/data-engine\/batches\/[^/]+$/);

    await expect(
      page.getByRole("heading", { name: /Ingestion batch/ }),
    ).toBeVisible();
    const header = page
      .getByRole("heading", { name: /Ingestion batch/ })
      .locator("..");
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
      await expect(
        page.getByRole("heading", { name: "Data Engine", exact: true }),
      ).toBeVisible();
      await page.goto(detailUrl);
      await expect(breadcrumb).toBeVisible();
    }
  });
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

test("retry recovers the Markets view after a network failure", async ({
  page,
}) => {
  await page.route("**/banks/*/market-data/views*", (route) =>
    route.abort("failed"),
  );
  await page.goto("/markets");
  await expect(
    page.getByText("Could not load data", { exact: true }),
  ).toBeVisible();
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
