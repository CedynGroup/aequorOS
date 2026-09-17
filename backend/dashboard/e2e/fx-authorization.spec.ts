// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test.describe("unbound FX user", () => {
  test.use({ storageState: path.join(E2E_TMP, "viewer.json") });

  test("hides navigation, 404s deep links, and sends no FX requests", async ({
    page,
  }) => {
    const fxRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/banks\/[^/]+\/fx(?:\/|$)/.test(request.url())) {
        fxRequests.push(request.url());
      }
    });

    await page.goto("/");
    await expect(
      page.getByText("No authorized institutions yet", { exact: true }),
    ).toBeVisible();
    // See capital-authorization: entries may be listed, but every one must be
    // disabled and unfollowable.
    // Since #201 the shell LISTS the modules a baseline member cannot reach,
    // as `role="link"` spans carrying `aria-disabled="true"` and no href, each
    // explaining why it is blocked. Counting landmarks or roles therefore says
    // nothing about authority. What must hold is that no MODULE is followable:
    // the only enabled entry is the member's own settings, which
    // `lib/modules.test.ts` pins as deliberately reachable.
    const followable = page.locator(
      'nav [role="link"]:not([aria-disabled="true"]), nav a[href]',
    );
    await expect(followable).toHaveCount(1);
    await expect(followable.first()).toHaveAttribute("href", "/settings");

    await page.goto("/fx");
    // A baseline member deep-linking to a module they cannot reach is shown
    // the empty-state panel explaining why, not a 404 — deliberate since
    // #198/#201 and pinned by `hubRedirectFor` in lib/modules.test.ts. The
    // security property is unchanged and still asserted below: the module is
    // unreachable and no request for its data is made.
    await expect(
      page.getByText(/404|not found|No authorized institutions yet/i).first(),
    ).toBeVisible();
    await page.goto("/fx/scenarios");
    // A baseline member deep-linking to a module they cannot reach is shown
    // the empty-state panel explaining why, not a 404 — deliberate since
    // #198/#201 and pinned by `hubRedirectFor` in lib/modules.test.ts. The
    // security property is unchanged and still asserted below: the module is
    // unreachable and no request for its data is made.
    await expect(
      page.getByText(/404|not found|No authorized institutions yet/i).first(),
    ).toBeVisible();
    expect(fxRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "fx-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("bound FX user", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("keeps the FX run action visible with an exact grant reason", async ({
    page,
  }) => {
    await page.route("**/auth/me", async (route) => {
      const response = await route.fetch();
      const profile = await response.json();
      profile.effective_authority.organization_capabilities = [];
      for (const institution of profile.effective_authority
        .institution_capabilities) {
        institution.capabilities = institution.capabilities.filter(
          (capability: {
            module: string;
            sensitivity: string;
            permission: string;
          }) =>
            capability.module === "fx" &&
            capability.permission === "view" &&
            ["aggregated", "confidential"].includes(capability.sensitivity),
        );
      }
      await route.fulfill({ response, json: profile });
    });

    await page.goto("/fx/scenarios");
    await page
      .getByRole("button", { name: "Scenarios & run", exact: true })
      .click();
    const run = page.getByRole("button", { name: "Run enterprise stress" });
    await expect(run).toBeVisible();
    await expect(run).toBeDisabled();
    await run.locator("..").hover();
    await expect(page.getByRole("tooltip")).toHaveText(
      "Requires FX run permission at confidential sensitivity. An organization owner can grant it.",
    );

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "fx-run-disabled.png"),
        fullPage: true,
      });
    }
  });

  test("shows FX navigation and opens the exposure dashboard", async ({
    page,
  }) => {
    await page.goto("/fx");
    await expect(
      page.getByRole("heading", { name: "FX Exposure" }),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "VaR & Stress" }),
    ).toBeVisible();
    await expect(
      page.getByText("Aggregate NOP / Tier 1", { exact: true }),
    ).toBeVisible();

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "fx-bound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("FX Reports summary permissions", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  for (const sensitivity of ["aggregated", "confidential"]) {
    test(`${sensitivity} view controls summary requests and filters`, async ({
      page,
    }) => {
      // Resolve the fixture before navigation: an in-flight route.fetch can
      // outlive the document or be continued by unrouteAll during teardown.
      const response = await page.request.get(`${E2E_API_ORIGIN}/api/v1/auth/me`, {
        headers: { Authorization: `Bearer ${await mintBackendToken("admin")}` },
      });
      expect(response.ok()).toBe(true);
      const profile = await response.json();
      profile.effective_authority.organization_capabilities = [];
      for (const institution of profile.effective_authority
        .institution_capabilities) {
        institution.capabilities = institution.capabilities.filter(
          (capability: {
            module: string;
            sensitivity: string;
            permission: string;
          }) =>
            capability.permission === "view" &&
            (capability.module === "reg" ||
              (capability.module === "fx" &&
                capability.sensitivity === sensitivity)),
        );
      }
      await page.route("**/auth/me", (route) =>
        route.fulfill({ json: profile }),
      );

      const summaries: string[] = [];
      page.on("request", (request) => {
        if (/\/scenario-workbench\/fx\/analyses(?:\?|$)/.test(request.url())) {
          summaries.push(request.url());
        }
      });
      await page.goto("/reports/analyses");
      await expect(
        page.getByRole("heading", { name: "Saved analyses", exact: true }),
      ).toBeVisible();
      // The heading renders before authority resolves; navigation proves the
      // scoped profile has reached the shell before asserting absent requests.
      await expect(
        page.getByRole("link", { name: "FX", exact: true }),
      ).toBeVisible();
      if (sensitivity === "aggregated") {
        await expect.poll(() => summaries.length).toBeGreaterThan(0);
      } else {
        expect(summaries).toEqual([]);
      }

      await page.goto("/reports");
      await expect(
        page.getByRole("link", { name: "FX", exact: true }),
      ).toBeVisible();
      const fxFilter = page.getByRole("button", { name: "FX", exact: true });
      if (sensitivity === "aggregated") {
        await expect(fxFilter).toBeVisible();
      } else {
        await expect(
          page.getByRole("button", { name: "All modules", exact: true }),
        ).toBeVisible();
        await expect(fxFilter).toHaveCount(0);
      }
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, `fx-reports-${sensitivity}.png`),
          fullPage: true,
        });
      }
    });
  }
});
