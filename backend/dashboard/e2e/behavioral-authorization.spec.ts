// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { writeFile } from "node:fs/promises";
import { E2E_TMP } from "../playwright.config";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const BEHAVIORAL_API = /\/banks\/[^/]+\/behavioral(?:\/|$)/;
const RUN_REASON = /Requires Behavioral Models · Confidential · Run/i;

type Capability = { module: string; sensitivity: string; permission: string };

/**
 * Narrow the signed-in user's projected authority to exactly the Behavioral
 * capabilities under test. The dashboard reads nothing but this projection,
 * so the journey proves what a real binding of that shape would show.
 */
async function projectBehavioralAuthority(
  page: Page,
  keep: (capability: Capability) => boolean,
) {
  await page.route("**/auth/me", async (route) => {
    const response = await route.fetch();
    const profile = await response.json();
    profile.effective_authority.organization_capabilities = [];
    for (const institution of profile.effective_authority
      .institution_capabilities) {
      institution.capabilities = institution.capabilities.filter(
        (capability: Capability) =>
          capability.module === "beh" && keep(capability),
      );
    }
    await route.fulfill({ response, json: profile });
  });
}

function recordBehavioralRequests(page: Page): string[] {
  const requests: string[] = [];
  page.on("request", (request) => {
    if (BEHAVIORAL_API.test(request.url())) requests.push(request.url());
  });
  return requests;
}

async function expectNotFound(page: Page, href: string) {
  await page.goto(href);
  await expect(page.getByText(/404|not found/i).first()).toBeVisible();
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test.describe("unbound Behavioral user", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("disables navigation and 404s deep links without Behavioral requests", async ({
    page,
  }) => {
    const requests = recordBehavioralRequests(page);

    await page.goto("/liquidity");
    await expect(
      page.getByRole("heading", { name: "Liquidity Cockpit" }),
    ).toBeVisible();
    // Permission-only gaps keep the entry visible but disabled, naming the
    // exact sentence the user lacks (docs/rbac.md, permission-only controls).
    const navEntry = page.getByRole("link", {
      name: "Behavioral",
      exact: true,
    });
    await expect(navEntry).toHaveAttribute("aria-disabled", "true");
    await navEntry.hover();
    await expect(
      page.getByRole("tooltip", {
        name: /Requires Behavioral Models · Aggregated · View/i,
      }),
    ).toBeVisible();
    await expectNotFound(page, "/behavioral");
    await expectNotFound(page, "/behavioral/nmd-duration");
    await expectNotFound(page, "/behavioral/liquidity");
    expect(requests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "behavioral-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Behavioral run-only analyst without view", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("cannot open the module by deep link and issues no requests", async ({
    page,
  }) => {
    await projectBehavioralAuthority(
      page,
      (capability) =>
        capability.permission === "run" &&
        capability.sensitivity === "confidential",
    );
    const requests = recordBehavioralRequests(page);

    await expectNotFound(page, "/behavioral/prepayment");
    await expectNotFound(page, "/behavioral");
    expect(requests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "behavioral-run-only-deep-link.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Behavioral reader without run permission", () => {
  test.use({ storageState: path.join(E2E_TMP, "admin.json") });

  test("keeps Retrain visible, disabled, and explained", async ({ page }) => {
    await projectBehavioralAuthority(
      page,
      (capability) =>
        capability.permission === "view" &&
        capability.sensitivity === "aggregated",
    );
    const trainRequests: string[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "POST" &&
        /\/behavioral\/[^/]+\/train$/.test(request.url())
      ) {
        trainRequests.push(request.url());
      }
    });

    await page.goto("/behavioral/nmd-duration");
    await expect(
      page.getByRole("heading", { name: "NMD Duration" }),
    ).toBeVisible();
    const retrain = page.getByRole("button", { name: "Retrain" });
    await expect(retrain).toBeVisible();
    await expect(retrain).toBeDisabled();
    const wrapper = retrain.locator("..");
    await wrapper.focus();
    await expect(wrapper).toBeFocused();
    await expect(wrapper).toHaveAccessibleDescription(RUN_REASON);
    await expect(page.getByRole("tooltip", { name: RUN_REASON })).toBeVisible();
    expect(trainRequests).toEqual([]);

    await page.goto("/behavioral/liquidity");
    await expect(
      page.getByRole("heading", { name: "Behavioral Liquidity" }),
    ).toBeVisible();

    if (evidenceDir) {
      await page.goto("/behavioral/nmd-duration");
      await expect(retrain).toBeDisabled();
      await retrain.locator("..").focus();
      await expect(
        page.getByRole("tooltip", { name: RUN_REASON }),
      ).toBeVisible();
      await page.screenshot({
        path: path.join(evidenceDir, "behavioral-reader-disabled-retrain.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Behavioral analyst", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("retrains a model with effective authority", async ({ page }) => {
    await page.goto("/behavioral/nmd-duration");
    const retrain = page.getByRole("button", { name: "Retrain" });
    await expect(retrain).toBeVisible();
    await expect(retrain).toBeEnabled();

    const trained = page.waitForResponse(
      (response) =>
        /\/behavioral\/nmd-duration\/train$/.test(response.url()) &&
        response.request().method() === "POST",
    );
    await retrain.click();
    const response = await trained;
    expect(response.status()).toBe(200);
    const result = await response.json();
    expect(result.modelVersion).toBeTruthy();
    expect(["ml", "baseline"]).toContain(result.method);
    await expect(retrain).toBeEnabled();

    if (evidenceDir) {
      await writeFile(
        path.join(evidenceDir, "behavioral-trained-model.json"),
        JSON.stringify(result, null, 2),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "behavioral-analyst-enabled-retrain.png"),
        fullPage: true,
      });
    }
  });
});
