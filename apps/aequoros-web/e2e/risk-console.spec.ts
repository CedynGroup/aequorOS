import { expect, test } from "playwright/test";

import { healthUrl, isRiskServiceReady } from "./support/backend";
import { RequestTracker } from "./support/request-tracker";
import { RiskConsolePage } from "./support/risk-console-page";
import { northstarCase } from "./support/test-data";

test.beforeEach(async ({ page, request }) => {
  test.skip(
    !(await isRiskServiceReady(request)),
    `Risk service is not ready at ${healthUrl}. Start and seed it before E2E.`,
  );

  await new RiskConsolePage(page).seedTenantStorage();
});

test("loads the queue and opens a selected case workspace", async ({
  page,
}) => {
  const console = new RiskConsolePage(page);

  await console.gotoQueue();
  await console.expectQueueLoaded();
  await console.openNorthstarFromQueue();

  await console.expectNorthstarWorkspace();
  await expect(page.getByText("Assign, unassign, and archive")).toBeVisible();
});

test("keeps queue filters in the URL search params", async ({ page }) => {
  const console = new RiskConsolePage(page);

  await console.gotoQueue();
  await console.searchCases("Northstar");

  await expect(page).toHaveURL(/q=Northstar/);
  await expect(console.northstarButton()).toBeVisible();
});

test("guides bulk actions from disabled state to selected-case dialog", async ({
  page,
}) => {
  const console = new RiskConsolePage(page);

  await console.gotoQueue();
  await console.expectQueueLoaded();

  await expect(
    page.getByRole("button", { name: "Select cases" }),
  ).toBeDisabled();

  await console.selectNorthstarInQueue();
  await expect(
    page.getByRole("button", { name: "Bulk actions (1)" }),
  ).toBeEnabled();

  await page.getByRole("button", { name: "Bulk actions (1)" }).click();

  await expect(
    page.getByRole("dialog", { name: "Bulk actions" }),
  ).toBeVisible();
  await expect(page.getByText("1 selected cases")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Apply action" }),
  ).toBeVisible();
});

test("renders financial workspace and findings for the selected case", async ({
  page,
}) => {
  const console = new RiskConsolePage(page);

  await console.gotoSelectedCase("financial");

  await expect(
    page.getByRole("heading", { name: northstarCase.title }),
  ).toBeVisible();
  await console.expectFinancialSections();
  await expect(page.getByText("Northstar Foods").first()).toBeVisible();

  await page.getByRole("tab", { name: "Findings" }).click();

  await expect(page).toHaveURL(/tab=findings/);
  await expect(page.getByText("Cash conversion cycle widened")).toBeVisible();
  await expect(page.getByText("Borrowing base support updated")).toBeVisible();
});

test("initializes, edits, reviews, copies, archives, and tenant-isolates scenarios", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  const console = new RiskConsolePage(page);
  await console.gotoSelectedCase("scenarios");
  const scenariosPanel = page.getByRole("tabpanel", { name: "Scenarios" });

  const initialize = page.getByRole("button", {
    name: "Initialize baseline and downside",
  });
  const baselineHeading = page.getByRole("heading", { name: "Baseline" });
  await expect(initialize.or(baselineHeading)).toBeVisible();
  if (await initialize.isVisible()) await initialize.click();
  await expect(baselineHeading).toBeVisible();
  const downside = page.getByRole("button", { name: /^Downside / });
  await expect(downside).toBeVisible();

  const growth = page
    .getByLabel("Revenue growth value", { exact: true })
    .first();
  await growth.fill((await growth.inputValue()) === "0.04" ? "0.05" : "0.04");
  await growth
    .locator("..")
    .getByRole("button", { name: "Save", exact: true })
    .click();
  await expect(scenariosPanel.getByText("Revenue growth saved")).toBeVisible();

  for (const button of await page
    .locator("button:enabled")
    .filter({ hasText: /^Review$/ })
    .all()) {
    await button.click();
  }
  await downside.click();
  for (const button of await page
    .locator("button:enabled")
    .filter({ hasText: /^Review$/ })
    .all()) {
    await button.click();
  }
  await expect(page.getByText("Ready for calculations")).toBeVisible();
  expect(
    await scenariosPanel.evaluate(
      (panel) => panel.scrollWidth <= panel.clientWidth,
    ),
  ).toBe(true);

  await page.getByLabel("Copy scenario name").fill("Downside liquidity copy");
  await page.getByRole("button", { name: "Copy scenario" }).click();
  await expect(scenariosPanel.getByText("Scenario copied")).toBeVisible();
  await page.getByRole("button", { name: "Archive scenario" }).click();
  await expect(scenariosPanel.getByText("Scenario archived")).toBeVisible();
  await expect(
    page.getByText("Downside liquidity copy", { exact: true }),
  ).not.toBeVisible();

  await page
    .getByLabel("Tenant org id")
    .fill("22222222-2222-4222-8222-222222222222");
  await page.getByLabel("User id").fill("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
  await expect(page.getByText("Case not found.")).toBeVisible();
});

test("renders JSON and HTML reports without deprecated endpoints", async ({
  page,
}) => {
  const requests = new RequestTracker(page);

  await page.goto(
    `/cases/${northstarCase.id}?tab=report&report=json&archived=false`,
  );

  await expect(page.getByRole("button", { name: "JSON" })).toBeVisible();
  await expect(
    page.getByText('"title": "Covenant review - Northstar Foods"'),
  ).toBeVisible();

  await page.getByRole("button", { name: "HTML" }).click();

  await expect(page).toHaveURL(/report=html/);
  await expect(
    page
      .frameLocator('iframe[title="Risk report HTML preview"]')
      .getByRole("heading", { name: northstarCase.title }),
  ).toBeVisible();

  expect(requests.hasDeprecatedRiskEndpoint()).toBe(false);
  expect(
    requests.caseReportRequests(northstarCase.id).length,
  ).toBeGreaterThanOrEqual(2);
  expect(requests.caseReportRequests(northstarCase.id)).toEqual(
    expect.arrayContaining([
      expect.objectContaining({
        headers: expect.objectContaining({ accept: "application/json" }),
      }),
      expect.objectContaining({
        headers: expect.objectContaining({ accept: "text/html" }),
      }),
    ]),
  );
});

test("desktop and mobile viewports render the primary console without clipping the route", async ({
  page,
}) => {
  const console = new RiskConsolePage(page);

  await page.setViewportSize({ width: 1440, height: 900 });
  await console.gotoSelectedCase();

  await expect(page.getByRole("heading", { name: "Case Queue" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: northstarCase.title }),
  ).toBeVisible();

  await console.expectMobileOverview();
});
