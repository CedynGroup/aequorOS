import { expect, test } from "playwright/test";

import { apiBaseUrl, healthUrl, isRiskServiceReady } from "./support/backend";
import { RequestTracker } from "./support/request-tracker";
import { RiskConsolePage } from "./support/risk-console-page";
import { completedCase, demoTenant, northstarCase } from "./support/test-data";

const evidenceDir = process.env.NO_MISTAKES_EVIDENCE_DIR;

async function captureEvidence(
  page: import("playwright/test").Page,
  name: string,
) {
  if (!evidenceDir) return;
  await page.screenshot({ path: `${evidenceDir}/${name}.png`, fullPage: true });
}

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
  await console.searchCases("Adom Textiles");

  await expect(page).toHaveURL(
    (url) => url.searchParams.get("q") === "Adom Textiles",
  );
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
  await expect(
    page.getByText("Adom Textiles & Garments Ltd").first(),
  ).toBeVisible();

  await page.getByRole("tab", { name: "Findings" }).click();

  await expect(page).toHaveURL(/tab=findings/);
  await expect(
    page.getByText("Minimum current ratio reported below covenant"),
  ).toBeVisible();
});

test("initializes, edits, reviews, copies, archives, and tenant-isolates scenarios", async ({
  page,
  request,
}) => {
  test.setTimeout(60_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const tracker = new RequestTracker(page);
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
  for (const viewport of [
    { width: 1440, height: 1000 },
    { width: 1280, height: 800 },
  ]) {
    await page.setViewportSize(viewport);
    const assumptionTable = page.getByTestId("assumption-table");
    const topBar = page.getByTestId("risk-console-top-bar");
    await expect(assumptionTable).toBeVisible();
    expect(
      await assumptionTable.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBe(true);
    expect(
      await topBar.evaluate(
        (element) => element.scrollWidth <= element.clientWidth,
      ),
    ).toBe(true);
    expect(
      await page.evaluate(
        () =>
          document.documentElement.scrollWidth <=
          document.documentElement.clientWidth,
      ),
    ).toBe(true);
    await captureEvidence(
      page,
      `scenario-assumptions-${viewport.width}x${viewport.height}`,
    );
  }
  const downside = page.getByRole("button", { name: /^Downside / });
  await expect(downside).toBeVisible();

  const growth = page
    .getByLabel("Revenue growth value", { exact: true })
    .first();
  await growth.fill((await growth.inputValue()) === "4" ? "5" : "4");
  await growth
    .locator("xpath=ancestor::tr")
    .getByRole("button", { name: "Save", exact: true })
    .click();
  await expect(scenariosPanel.getByText("Revenue growth saved")).toBeVisible();

  const reviewButtons = page.getByRole("button", {
    name: "Review",
    exact: true,
  });
  for (let index = 0; index < (await reviewButtons.count()); index += 1) {
    if (await reviewButtons.nth(index).isEnabled()) {
      await reviewButtons.nth(index).click();
    }
  }
  await downside.click();
  for (let index = 0; index < (await reviewButtons.count()); index += 1) {
    if (await reviewButtons.nth(index).isEnabled()) {
      await reviewButtons.nth(index).click();
    }
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
  const tenantHeaders = {
    "X-Org-Id": demoTenant.orgId,
    "X-User-Id": demoTenant.userId,
  };
  const copiedWorkspaceResponse = await request.get(
    `${apiBaseUrl}/cases/${northstarCase.id}/scenarios`,
    { headers: tenantHeaders },
  );
  const copiedWorkspace = await copiedWorkspaceResponse.json();
  const copiedScenario = copiedWorkspace.scenarios.find(
    (scenario: { name: string }) => scenario.name === "Downside liquidity copy",
  );
  for (const assumption of copiedScenario.assumptions) {
    const reviewed = await request.post(
      `${apiBaseUrl}/cases/${northstarCase.id}/scenarios/${copiedScenario.id}/assumptions/${assumption.id}/review`,
      {
        headers: tenantHeaders,
        data: { reason: "Prepare archived forecast evidence" },
      },
    );
    expect(reviewed.ok()).toBe(true);
  }
  const archivedRunResponse = await request.post(
    `${apiBaseUrl}/cases/${northstarCase.id}/calculation-runs`,
    {
      headers: tenantHeaders,
      data: { scenario_id: copiedScenario.id, forecast_periods: 3 },
    },
  );
  expect(archivedRunResponse.ok()).toBe(true);
  const archivedRun = await archivedRunResponse.json();
  await page.getByRole("button", { name: "Archive scenario" }).click();
  await expect(scenariosPanel.getByText("Scenario archived")).toBeVisible();
  await expect(
    page.getByText("Downside liquidity copy", { exact: true }),
  ).not.toBeVisible();

  const archivedResponse = await request.get(
    `${apiBaseUrl}/cases/${northstarCase.id}/scenarios?include_archived=true`,
    {
      headers: {
        "X-Org-Id": demoTenant.orgId,
        "X-User-Id": demoTenant.userId,
      },
    },
  );
  expect(archivedResponse.ok()).toBe(true);
  const archivedWorkspace = await archivedResponse.json();
  const archivedScenario = archivedWorkspace.scenarios.find(
    (scenario: { name: string }) => scenario.name === "Downside liquidity copy",
  );
  expect(archivedScenario).toBeTruthy();
  const archivedAssumption = archivedScenario.assumptions[0];
  await page.goto(
    `/cases/${northstarCase.id}?tab=scenarios#scenario-${archivedScenario.id}-assumption-${archivedAssumption.id}`,
  );
  await expect(page.getByText("Archived scenario audit mode")).toBeVisible();
  await expect(
    scenariosPanel.getByText("Archived", { exact: true }),
  ).toBeVisible();
  await expect(
    scenariosPanel.getByRole("button", { name: "Save details" }),
  ).toHaveCount(0);
  await expect(
    scenariosPanel.getByRole("button", { name: "Review" }),
  ).toHaveCount(0);
  await expect(
    scenariosPanel.getByRole("button", { name: "Add assumption" }),
  ).toHaveCount(0);

  await console.gotoSelectedCase("liquidity");
  await page.getByLabel("Liquidity scenario").click();
  await page
    .getByRole("option", { name: "Downside liquidity copy · Archived" })
    .click();
  const liquidityPanel = page.getByRole("tabpanel", { name: "Liquidity" });
  await expect(
    liquidityPanel.getByText("Archived", { exact: true }),
  ).toBeVisible();
  await expect(
    liquidityPanel.getByRole("button", { name: "Acknowledge" }),
  ).toHaveCount(0);
  await expect(
    liquidityPanel.getByRole("button", { name: "Dismiss" }),
  ).toHaveCount(0);

  await page.goto(`/cases/${northstarCase.id}?tab=calculations`);
  await page
    .getByRole("button", {
      name: /Downside liquidity copy/,
    })
    .click();
  await expect(page.getByText("Archived forecast audit")).toBeVisible();
  await expect(page.getByText("Archived scenario · read only")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run forecast" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Rerun current inputs" }),
  ).toHaveCount(0);

  await page.goto(
    `/cases/${northstarCase.id}?tab=calculations#calculation-run-${archivedRun.id}-forecast-period-1`,
  );
  const forecastPanel = page.getByRole("tabpanel", { name: "Forecast" });
  await expect(page.getByText("Archived forecast audit")).toBeVisible();
  await expect(
    forecastPanel.getByText("Archived", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("Archived scenario · read only")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run forecast" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Rerun current inputs" }),
  ).toHaveCount(0);

  const calculationMutationCount = () =>
    tracker.requests.filter(
      (tracked) =>
        tracked.method === "POST" &&
        tracked.url.includes(`/cases/${northstarCase.id}/calculation-runs`),
    ).length;
  const mutationCountBeforeDemo = calculationMutationCount();
  await page.getByRole("button", { name: "Demo seed data" }).click();
  await expect(
    page.getByText("Mutation unavailable in demo mode"),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run forecast" }),
  ).toBeDisabled();
  await expect(page.getByText("Demo mode · read only")).toBeVisible();
  expect(calculationMutationCount()).toBe(mutationCountBeforeDemo);

  await page.reload();
  await expect(page.getByText("Archived forecast audit")).toBeVisible();

  await console.gotoSelectedCase("liquidity");
  await page.getByLabel("Liquidity scenario").click();
  await page
    .getByRole("option", { name: "Downside liquidity copy · Archived" })
    .click();
  await expect(page.getByText("Archived", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Acknowledge" })).toHaveCount(
    0,
  );
  await expect(page.getByRole("button", { name: "Dismiss" })).toHaveCount(0);

  await page.goto(`/cases/${northstarCase.id}?tab=calculations`);
  await page
    .getByRole("button", {
      name: /Downside liquidity copy/,
    })
    .first()
    .click();
  await expect(page.getByText("Archived forecast audit")).toBeVisible();
  await expect(page.getByText("Archived scenario · read only")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run forecast" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Rerun current inputs" }),
  ).toHaveCount(0);

  await page.goto(
    `/cases/${northstarCase.id}?tab=calculations#calculation-run-${archivedRun.id}-forecast-period-1`,
  );
  await expect(page.getByText("Archived forecast audit")).toBeVisible();
  await expect(page.getByText("Archived", { exact: true })).toBeVisible();
  await expect(page.getByText("Archived scenario · read only")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Run forecast" }),
  ).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Rerun current inputs" }),
  ).toHaveCount(0);

  await page.getByRole("combobox", { name: "Organization" }).click();
  await page.getByRole("option", { name: "AequorOS Isolated Tenant" }).click();
  await expect(page.getByText("No case selected")).toBeVisible();
});

test("runs, reruns, fails, and reviews persisted balance-sheet forecasts with tenant isolation", async ({
  page,
  request,
}) => {
  const tenantHeaders = {
    "X-Org-Id": demoTenant.orgId,
    "X-User-Id": demoTenant.userId,
  };
  let scenarioResponse = await request.get(
    `${apiBaseUrl}/cases/${northstarCase.id}/scenarios`,
    { headers: tenantHeaders },
  );
  let workspace = await scenarioResponse.json();
  if (!workspace.scenarios.length) {
    scenarioResponse = await request.post(
      `${apiBaseUrl}/cases/${northstarCase.id}/scenarios/initialize`,
      {
        headers: tenantHeaders,
        data: { reason: "Prepare forecast e2e" },
      },
    );
    workspace = await scenarioResponse.json();
  }
  const baseline = workspace.scenarios.find(
    (scenario: { scenario_type: string }) =>
      scenario.scenario_type === "baseline",
  );
  for (const assumption of baseline.assumptions) {
    const reviewed = await request.post(
      `${apiBaseUrl}/cases/${northstarCase.id}/scenarios/${baseline.id}/assumptions/${assumption.id}/review`,
      {
        headers: tenantHeaders,
        data: { reason: "Approve forecast e2e input" },
      },
    );
    expect(reviewed.ok()).toBe(true);
  }

  const console = new RiskConsolePage(page);
  await console.gotoSelectedCase("calculations");
  await expect(
    page.getByText("No calculation runs").or(page.getByText("Run history")),
  ).toBeVisible();
  await page
    .getByRole("button", { name: /Baseline succeeded/ })
    .first()
    .click();
  await page.getByLabel("Forecast scenario").click();
  await page.getByRole("option", { name: "Baseline" }).click();
  await page.getByRole("button", { name: "Run forecast" }).click();
  await expect(page.getByText("Forecast completed")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Rerun current inputs" }),
  ).toBeVisible();
  await expect(page.getByText("Projected balance sheet outputs")).toBeVisible();
  await expect(
    page.locator('svg[aria-label="Balance-sheet trajectory chart"]'),
  ).toBeVisible();
  const successfulRunsResponse = await request.get(
    `${apiBaseUrl}/cases/${northstarCase.id}/calculation-runs?scenario_id=${baseline.id}`,
    { headers: tenantHeaders },
  );
  const successfulRuns = await successfulRunsResponse.json();
  const successfulHash = successfulRuns.runs[0].input_hash;

  await page.getByRole("tab", { name: "Scenarios" }).click();
  const growth = page
    .getByLabel("Revenue growth value", { exact: true })
    .first();
  await growth.fill((await growth.inputValue()) === "7" ? "8" : "7");
  await growth
    .locator("xpath=ancestor::tr")
    .getByRole("button", { name: "Save", exact: true })
    .click();
  await growth
    .locator("xpath=ancestor::tr")
    .getByRole("button", { name: "Review", exact: true })
    .click();

  await page.getByRole("tab", { name: "Forecast" }).click();
  await page
    .getByRole("button", { name: /Baseline succeeded/ })
    .first()
    .click();
  await page.getByRole("button", { name: "Rerun current inputs" }).click();
  await expect(page.getByText("Projected balance sheet outputs")).toBeVisible();
  const rerunResponse = await request.get(
    `${apiBaseUrl}/cases/${northstarCase.id}/calculation-runs?scenario_id=${baseline.id}`,
    { headers: tenantHeaders },
  );
  const reruns = await rerunResponse.json();
  expect(
    reruns.runs.some(
      (candidate: { input_hash: string }) =>
        candidate.input_hash !== successfulHash,
    ),
  ).toBe(true);

  await page.getByRole("tab", { name: "Scenarios" }).click();
  await growth.fill((await growth.inputValue()) === "9" ? "10" : "9");
  await growth
    .locator("xpath=ancestor::tr")
    .getByRole("button", { name: "Save", exact: true })
    .click();
  await page.getByRole("tab", { name: "Forecast" }).click();
  await page
    .getByRole("button", { name: /Baseline succeeded/ })
    .first()
    .click();
  await page.getByRole("button", { name: "Rerun current inputs" }).click();
  await expect(page.getByText("scenario_not_ready")).toBeVisible();
  await expect(page.getByText("Prior valid output preserved")).toBeVisible();

  await page.reload();
  await expect(page.getByText("scenario_not_ready")).toBeVisible();
  await page
    .getByRole("button", { name: "Review the latest successful forecast" })
    .click();
  await expect(page.getByText("Projected balance sheet outputs")).toBeVisible();

  await page.getByRole("combobox", { name: "Organization" }).click();
  await page.getByRole("option", { name: "AequorOS Isolated Tenant" }).click();
  await expect(page.getByText("No case selected")).toBeVisible();
});

test("reviews liquidity metrics, evidence, finding status, and tenant isolation", async ({
  page,
  request,
}) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  const tenantHeaders = {
    "X-Org-Id": demoTenant.orgId,
    "X-User-Id": demoTenant.userId,
  };
  let scenarioResponse = await request.get(
    `${apiBaseUrl}/cases/${northstarCase.id}/scenarios`,
    { headers: tenantHeaders },
  );
  let workspace = await scenarioResponse.json();
  if (!workspace.scenarios.length) {
    scenarioResponse = await request.post(
      `${apiBaseUrl}/cases/${northstarCase.id}/scenarios/initialize`,
      {
        headers: tenantHeaders,
        data: { reason: "Prepare liquidity e2e" },
      },
    );
    workspace = await scenarioResponse.json();
  }
  const baseline = workspace.scenarios.find(
    (scenario: { scenario_type: string }) =>
      scenario.scenario_type === "baseline",
  );
  for (const assumption of baseline.assumptions) {
    const reviewed = await request.post(
      `${apiBaseUrl}/cases/${northstarCase.id}/scenarios/${baseline.id}/assumptions/${assumption.id}/review`,
      {
        headers: tenantHeaders,
        data: { reason: "Approve liquidity e2e input" },
      },
    );
    expect(reviewed.ok()).toBe(true);
  }
  const run = await request.post(
    `${apiBaseUrl}/cases/${northstarCase.id}/calculation-runs`,
    {
      headers: tenantHeaders,
      data: { scenario_id: baseline.id, forecast_periods: 3 },
    },
  );
  expect(run.ok()).toBe(true);
  const runPayload = await run.json();
  expect(runPayload.status).toBe("succeeded");

  const console = new RiskConsolePage(page);
  await console.gotoSelectedCase("liquidity");
  await expect(page.getByText("Liquidity risk summary")).toBeVisible();
  await expect(page.getByLabel("Liquidity scenario")).toBeVisible();
  await expect(page.getByLabel("Liquidity forecast run")).toBeVisible();
  await expect(
    page.getByText(/Baseline · deterministic forecast/),
  ).toBeVisible();
  await expect(page.getByText("Minimum cash balance")).toBeVisible();
  await expect(
    page.locator('svg[aria-label="Liquidity sources coverage chart"]'),
  ).toBeVisible();
  await expect(page.getByText(/Supporting evidence \(/).first()).toBeVisible();
  await page
    .getByText(/Supporting evidence \(/)
    .first()
    .click();
  await expect(
    page.getByRole("link", { name: /Forecast period/ }).first(),
  ).toBeVisible();
  await captureEvidence(page, "liquidity-summary-and-evidence");

  const acknowledge = page.getByRole("button", { name: "Acknowledge" }).first();
  await expect(acknowledge).toBeEnabled();
  await acknowledge.click();
  await expect(page.getByText("Liquidity finding acknowledged")).toBeVisible();
  await expect(page.getByText("Terminal finding · read only")).toBeVisible();
  await captureEvidence(page, "liquidity-acknowledged-read-only");

  await page.getByRole("combobox", { name: "Organization" }).click();
  await page.getByRole("option", { name: "AequorOS Isolated Tenant" }).click();
  await expect(page.getByText("No case selected")).toBeVisible();
});

test("renders liquidity empty and error states", async ({ page }) => {
  const summaryPattern = `**/api/v1/cases/${northstarCase.id}/liquidity/summary**`;
  await page.route(summaryPattern, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        case_id: northstarCase.id,
        scenario_id: null,
        calculation_run_id: null,
        calculation_input_hash: null,
        analysis_version: null,
        status: "not_calculated",
        currency: null,
        as_of_date: null,
        metrics: [],
        findings: [],
        generated_at: null,
      }),
    });
  });
  const console = new RiskConsolePage(page);
  await console.gotoSelectedCase("liquidity");
  await expect(
    page.getByText(
      "Liquidity analysis not available for this run — rerun to generate it.",
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "Open Forecast to rerun" }),
  ).toHaveAttribute(
    "href",
    new RegExp(
      `^/cases/${northstarCase.id}\\?tab=calculations#calculation-run-[0-9a-f-]+-forecast-period-1$`,
    ),
  );

  await page.unroute(summaryPattern);
  await page.route(summaryPattern, async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        case_id: northstarCase.id,
        scenario_id: "11111111-1111-4111-8111-111111111111",
        calculation_run_id: "22222222-2222-4222-8222-222222222222",
        calculation_input_hash: "a".repeat(64),
        analysis_version: "liquidity-v1.0.0",
        status: "ready",
        currency: "USD",
        as_of_date: "2026-07-14",
        metrics: [
          {
            key: "minimum_sources_coverage",
            label: "Minimum sources coverage",
            value: null,
            unit: "ratio",
            availability: "unavailable",
            diagnostic:
              "Sources coverage is unavailable because projected outflows plus debt repayment must be positive; period 1 uses 0.0000. The ratio is undefined and was excluded from threshold classification.",
            period_number: null,
            period_end: null,
            description: "Lowest sources coverage across the forecast.",
          },
        ],
        findings: [],
        generated_at: "2026-07-14T00:00:00Z",
      }),
    });
  });
  await page.reload();
  await expect(
    page.getByText(
      "The selected forecast did not cross an MVP liquidity risk threshold.",
    ),
  ).toBeVisible();
  await expect(page.getByText("Not available")).toBeVisible();
  await expect(page.getByText(/period 1 uses 0\.0000/)).toBeVisible();

  await page.unroute(summaryPattern);
  await page.route(summaryPattern, async (route) => {
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "liquidity_unavailable",
          message: "Liquidity analysis is temporarily unavailable.",
          details: null,
        },
        request_id: "e2e-liquidity-error",
      }),
    });
  });
  await page.reload();
  await expect(page.getByText("Request failed")).toBeVisible();
});

test("ignores malformed calculation evidence links", async ({ page }) => {
  let malformedRunRequests = 0;
  await page.route("**/calculation-runs/not-a-uuid", async (route) => {
    malformedRunRequests += 1;
    await route.abort();
  });

  await page.goto(
    `/cases/${northstarCase.id}?tab=calculations#calculation-run-not-a-uuid-forecast-period-0`,
  );

  await expect(page.getByRole("tabpanel", { name: "Forecast" })).toBeVisible();
  expect(malformedRunRequests).toBe(0);
});

test("renders JSON and HTML reports without deprecated endpoints", async ({
  page,
}) => {
  const requests = new RequestTracker(page);

  await page.goto(
    `/cases/${completedCase.id}?tab=report&report=json&archived=false`,
  );

  await expect(page.getByRole("button", { name: "JSON" })).toBeVisible();
  await expect(
    page.getByText(`"title": "${completedCase.title}"`),
  ).toBeVisible();

  await page.getByRole("button", { name: "HTML" }).click();

  await expect(page).toHaveURL(/report=html/);
  await expect(
    page
      .frameLocator('iframe[title="Risk report HTML preview"]')
      .getByRole("heading", { name: completedCase.title }),
  ).toBeVisible();

  expect(requests.hasDeprecatedRiskEndpoint()).toBe(false);
  expect(
    requests.caseReportRequests(completedCase.id).length,
  ).toBeGreaterThanOrEqual(2);
  expect(requests.caseReportRequests(completedCase.id)).toEqual(
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

  await expect(
    page.getByRole("button", { name: "Show case queue" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Case Queue" })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("heading", { name: northstarCase.title }),
  ).toBeVisible();
  await captureEvidence(page, "case-detail-with-queue-collapsed");

  await page.getByRole("button", { name: "Show case queue" }).click();
  await expect(page.getByRole("heading", { name: "Case Queue" })).toBeVisible();

  await console.expectMobileOverview();
});
