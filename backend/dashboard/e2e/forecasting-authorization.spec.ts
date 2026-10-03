// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { writeFile } from "node:fs/promises";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const FORECASTING_ROUTE = /\/banks\/[^/]+\/(?:forecast|reverse-stress)(?:\/|$)/;
const RUN_REASON = /Requires Forecasting · Confidential · Run/i;
const CONFIDENTIAL_VIEW_REASON = /Requires Forecasting · Confidential · View/i;
// `next dev` compiles each route on its first visit; the first assertion
// after a navigation waits for that rather than for the product.
const FIRST_PAINT = { timeout: 60_000 };

type Capability = { module: string; sensitivity: string; permission: string };

/**
 * Narrow the signed-in admin's projected authority to exactly the Forecasting
 * capabilities under test. The backend still decides every request; this only
 * shapes what the dashboard believes it may offer.
 */
async function projectForecasting(
  page: Page,
  keep: (capability: Capability) => boolean,
) {
  await page.route("**/auth/me", async (route) => {
    const response = await route.fetch();
    const profile = await response.json();
    profile.effective_authority.organization_capabilities = [];
    for (const institution of profile.effective_authority
      .institution_capabilities) {
      institution.capabilities = institution.capabilities.filter(keep);
    }
    // A navigation can cancel the request while the profile is in flight.
    await route.fulfill({ response, json: profile }).catch(() => undefined);
  });
}

async function expectDisabledWithReason(
  page: Page,
  name: string,
  reason: RegExp,
) {
  // A page with no result yet offers the same control twice (header and
  // empty state); the first is the header action.
  const button = page.getByRole("button", { name }).first();
  await expect(button).toBeVisible();
  await expect(button).toBeDisabled();
  const wrapper = button.locator("..");
  await wrapper.focus();
  await expect(wrapper).toBeFocused();
  await expect(wrapper).toHaveAccessibleDescription(reason);
  await expect(page.getByRole("tooltip", { name: reason })).toBeVisible();
}

async function expectDisabledWorkspace(page: Page, reason: RegExp) {
  await expect(
    page.getByRole("region", { name: "Forecasting workspace" }),
  ).toBeVisible(FIRST_PAINT);
  await expect(
    page.getByRole("navigation", { name: "Module sections" }),
  ).toBeVisible();
  await expectDisabledWithReason(page, "View Forecasting", reason);
  await expect(page.getByRole("tooltip")).toContainText("Org Owner");
  await expect(page.getByRole("tooltip")).toContainText("Settings → Members");
}

test.afterEach(async ({ page }) => {
  // Abandon, never await, a profile handler the last navigation cancelled.
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test.describe("unbound Forecasting user", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("keeps disabled workspaces and navigation without Forecasting requests", async ({
    page,
  }) => {
    const forecastingRequests: string[] = [];
    page.on("request", (request) => {
      if (FORECASTING_ROUTE.test(request.url())) {
        forecastingRequests.push(request.url());
      }
    });

    for (const deepLink of [
      "/forecasting",
      "/forecasting/scenario",
      "/forecasting/reverse-stress",
      "/forecasting/optimizer",
    ]) {
      await page.goto(deepLink);
      await expectDisabledWorkspace(page, /Requires Forecasting .* View/i);
    }
    await expect(
      page.getByRole("link", { name: "Forecasting", exact: true }),
    ).toHaveAttribute("aria-disabled", "true");
    expect(forecastingRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Forecasting reader without run permission", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("keeps every execution control visible, disabled, and explained", async ({
    page,
  }) => {
    // Several first-visit `next dev` compilations in one journey.
    test.slow();
    await projectForecasting(
      page,
      (capability) =>
        capability.module === "fcst" &&
        capability.permission === "view" &&
        ["aggregated", "confidential"].includes(capability.sensitivity),
    );
    const mutations: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        FORECASTING_ROUTE.test(request.url())
      ) {
        mutations.push(request.url());
      }
    });

    await page.goto("/forecasting");
    await expect(
      page.getByRole("heading", { name: "Balance Sheet Forecast" }),
    ).toBeVisible(FIRST_PAINT);
    await expectDisabledWithReason(page, "Run forecast", RUN_REASON);
    if (evidenceDir) {
      await page.waitForLoadState("networkidle");
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-reader-disabled-run.png"),
        fullPage: true,
      });
    }

    await page.goto("/forecasting/scenario");
    await expect(
      page.getByRole("heading", { name: "Scenario Manager" }),
    ).toBeVisible(FIRST_PAINT);
    await expectDisabledWithReason(page, "Run Base case", RUN_REASON);

    await page.goto("/forecasting/reverse-stress");
    await expect(
      page.getByRole("heading", { name: "Reverse Stress Testing" }),
    ).toBeVisible(FIRST_PAINT);
    await expectDisabledWithReason(page, "Run reverse stress", RUN_REASON);

    await page.goto("/forecasting/optimizer");
    await expect(
      page.getByRole("heading", { name: "Strategy Optimizer" }),
    ).toBeVisible(FIRST_PAINT);
    await expectDisabledWithReason(page, "Run optimizer", RUN_REASON);

    await page.goto("/forecasting/whatif");
    const whatIfRun = page.getByRole("button", { name: /^(Run|Re-run) / });
    await expect(whatIfRun.first()).toBeVisible(FIRST_PAINT);
    await expect(whatIfRun.first()).toBeDisabled();
    // Initial data can replace the control and discard focus during rendering.
    await page.waitForLoadState("networkidle");
    await whatIfRun.first().locator("..").focus();
    await expect(whatIfRun.first().locator("..")).toHaveAccessibleDescription(
      RUN_REASON,
    );

    expect(mutations).toEqual([]);
    if (evidenceDir) {
      await page.waitForLoadState("networkidle");
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-reader-whatif.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Forecasting confidential-only reader", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("explains the missing preset grant beside the disabled scenario run", async ({
    page,
  }) => {
    test.slow();
    await projectForecasting(
      page,
      (capability) =>
        capability.module === "fcst" &&
        capability.permission === "view" &&
        capability.sensitivity === "confidential",
    );
    const requests: string[] = [];
    page.on("request", (request) => {
      if (FORECASTING_ROUTE.test(request.url())) requests.push(request.url());
    });
    await page.goto("/forecasting/scenario");
    await expect(
      page.getByRole("heading", { name: "Scenario Manager" }),
    ).toBeVisible(FIRST_PAINT);
    await expectDisabledWithReason(
      page,
      "Run scenario",
      /Requires Forecasting · Aggregated · View/i,
    );
    for (const [href, label] of [
      ["/forecasting/nii", "NII Forecast"],
      ["/forecasting/optimizer", "Optimizer"],
      ["/forecasting/whatif", "What-if Lab"],
    ]) {
      await page.goto(href);
      await expectDisabledWorkspace(
        page,
        /Requires Forecasting · Aggregated · View/i,
      );
      await expect(
        page.getByRole("link", { name: label, exact: true }),
      ).toHaveAttribute("aria-disabled", "true");
      await expect(
        page.getByText("No succeeded forecast runs yet"),
      ).toHaveCount(0);
    }
    expect(requests).toEqual([]);
  });
});

test.describe("Forecasting aggregated-only reader", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("sees summaries, never requests run detail, and finds detail tabs disabled", async ({
    page,
  }) => {
    // Several first-visit `next dev` compilations in one journey.
    test.slow();
    await projectForecasting(
      page,
      (capability) =>
        capability.module === "fcst" &&
        capability.permission === "view" &&
        capability.sensitivity === "aggregated",
    );
    const detailRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/forecast\/runs\/[^/?]+/.test(request.url()) ||
        /\/reverse-stress\/latest/.test(request.url())
      ) {
        detailRequests.push(request.url());
      }
    });

    await page.goto("/forecasting");
    await expect(
      page.getByRole("heading", { name: "Balance Sheet Forecast" }),
    ).toBeVisible(FIRST_PAINT);
    for (const tab of [
      "NII Forecast",
      "Scenarios",
      "What-if Lab",
      "Reverse Stress",
      "Optimizer",
    ]) {
      const link = page.getByRole("link", { name: tab, exact: true });
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute("aria-disabled", "true");
      await link.hover();
      await expect(
        page.getByRole("tooltip", { name: CONFIDENTIAL_VIEW_REASON }),
      ).toBeVisible();
      await expect(link).toHaveAccessibleDescription(CONFIDENTIAL_VIEW_REASON);
    }
    await expect(
      page.getByRole("link", { name: "Assumptions", exact: true }),
    ).not.toHaveAttribute("aria-disabled", "true");

    await page.goto("/forecasting/reverse-stress");
    await expectDisabledWorkspace(page, CONFIDENTIAL_VIEW_REASON);
    expect(detailRequests).toEqual([]);

    if (evidenceDir) {
      await page.goto("/forecasting");
      await expect(
        page.getByRole("link", { name: "NII Forecast", exact: true }),
      ).toHaveAttribute("aria-disabled", "true", FIRST_PAINT);
      await page.waitForLoadState("networkidle");
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-aggregated-reader.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Forecasting analyst", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("runs a forecast and a reverse-stress frontier with effective authority", async ({
    page,
  }) => {
    // Several first-visit `next dev` compilations in one journey.
    test.slow();
    await page.goto("/forecasting");
    const runButton = page.getByRole("button", { name: "Run forecast" });
    await expect(runButton).toBeVisible();
    await expect(runButton).toBeEnabled();

    const created = page.waitForResponse(
      (response) =>
        /\/forecast\/runs$/.test(response.url()) &&
        response.request().method() === "POST",
    );
    await runButton.click();
    const response = await created;
    expect(response.status()).toBe(201);
    const run = await response.json();
    expect(run.module ?? "forecast").toBe("forecast");
    expect(run.status).toBe("succeeded");
    await expect(
      page.getByRole("heading", { name: /projection path/i }),
    ).toBeVisible();

    await page.goto("/forecasting/reverse-stress");
    const frontierButton = page.getByRole("button", {
      name: "Run reverse stress",
    });
    await expect(frontierButton.first()).toBeEnabled();
    const frontierCreated = page.waitForResponse(
      (response) =>
        /\/reverse-stress\/runs$/.test(response.url()) &&
        response.request().method() === "POST",
    );
    await frontierButton.first().click();
    const frontier = await frontierCreated;
    expect(frontier.status()).toBe(201);

    if (evidenceDir) {
      await writeFile(
        path.join(evidenceDir, "forecasting-executed-runs.json"),
        JSON.stringify(
          { forecast: run, reverseStress: await frontier.json() },
          null,
          2,
        ),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-analyst-enabled-run.png"),
        fullPage: true,
      });
    }
  });
});
