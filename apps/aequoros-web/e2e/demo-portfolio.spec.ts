import { expect, test, type Page } from "playwright/test";

import { healthUrl, isRiskServiceReady } from "./support/backend";
import { RiskConsolePage } from "./support/risk-console-page";
import {
  breachingCase,
  completedCase,
  liquidityCase,
} from "./support/test-data";

async function expectNoVisibleUuid(page: Page) {
  expect(await page.locator("body").innerText()).not.toMatch(
    /\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b/i,
  );
}

test.beforeEach(async ({ page, request }) => {
  test.skip(
    !(await isRiskServiceReady(request)),
    `Risk service is not ready at ${healthUrl}. Start and reset the demo before E2E.`,
  );
  await new RiskConsolePage(page).seedTenantStorage();
});

test("walks the pristine narrative portfolio from queue through report", async ({
  page,
}) => {
  const console = new RiskConsolePage(page);

  await console.gotoQueue();
  await expect(page.getByText("4 cases")).toBeVisible();
  for (const borrower of [
    "Volta Aluminium Industries Plc",
    "Adom Textiles & Garments Ltd",
    "Kivu Fresh Produce Logistics Ltd",
    "Baobab Health Distribution SA",
  ]) {
    await expect(page.getByText(new RegExp(borrower)).first()).toBeVisible();
  }
  await expectNoVisibleUuid(page);

  await page.goto(`/cases/${breachingCase.id}?tab=financial`);
  await expect(page.getByText("Unmapped source rows")).toBeVisible();
  await expect(page.getByText(/Current assets/)).toBeVisible();
  await expect(page.getByText("0.910000")).toBeVisible();
  await expectNoVisibleUuid(page);

  await page.getByRole("tab", { name: "Findings" }).click();
  await expect(
    page.getByText("Minimum current ratio reported below covenant"),
  ).toBeVisible();
  await page.getByText("Source evidence (1)").click();
  await expect(
    page.getByRole("link", { name: "Open linked financial record" }),
  ).toBeVisible();

  await page.getByRole("tab", { name: "Scenarios" }).click();
  await page
    .getByRole("button", { name: /^Downside — collections stress/ })
    .click();
  await expect(page.getByText("Ready for calculations")).toBeVisible();

  await page.getByRole("tab", { name: "Forecast" }).click();
  await page
    .getByRole("button", { name: /Downside — collections stress failed/ })
    .click();
  await expect(page.getByText("scenario_not_ready")).toBeVisible();
  await expectNoVisibleUuid(page);

  await page.goto(`/cases/${liquidityCase.id}?tab=liquidity`);
  await expect(page.getByText("Liquidity risk summary")).toBeVisible();
  await page.getByLabel("Liquidity scenario").click();
  await page
    .getByRole("option", { name: "Downside — collections stress" })
    .click();
  await expect(
    page.getByText(/Downside — collections stress/).first(),
  ).toBeVisible();
  await expect(page.getByText(/cash shortfall/i).first()).toBeVisible();
  await expectNoVisibleUuid(page);

  await page.getByRole("tab", { name: "Capital" }).click();
  await expect(page.getByText("Baseline vs downside")).toBeVisible();
  await expect(page.getByText("Downside delta").first()).toBeVisible();
  await expectNoVisibleUuid(page);

  await page.goto(`/cases/${completedCase.id}?tab=decisions`);
  await expect(
    page.getByText("Approved", { exact: true }).first(),
  ).toBeVisible();
  await page.getByRole("button", { name: "Reports" }).click();
  await page.getByRole("button", { name: "HTML" }).click();
  await expect(
    page
      .frameLocator('iframe[title="Risk report HTML preview"]')
      .getByRole("heading", { name: completedCase.title }),
  ).toBeVisible();
  await expectNoVisibleUuid(page);
});
