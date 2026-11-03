// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
//
// Ordered on purpose (workers: 1): the analyst journey uploads the market data
// the reader journeys then inspect, so it runs first.
import { expect, type Page } from "@playwright/test";
import { test } from "./support/story-identities";
import { expectBankStatus } from "./support/real-authority";
import { MarketDataOverlayListReadFromJSON } from "@aequoros/risk-service-api";
import { biApi, SAMPLE_BANK_ID } from "./support/bi";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { writeFile } from "node:fs/promises";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { requireObjectStorage } from "./support/object-storage";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const BACKEND_DIR = path.join(__dirname, "..", "..");
const MARKET_DATA_REQUEST =
  /\/(?:market-data|implied-rating)(?:\/|$|\?)|\/market-data\/templates\//;
// The as-of date the shared full-coverage workbook fixture is built for.
const UPLOAD_AS_OF = "2026-06-30";

/** Build the four-sheet upload workbook the backend's own fixture produces. */
async function fullCoverageWorkbook(): Promise<Buffer> {
  const { stdout } = await promisify(execFile)(
    "uv",
    [
      "run",
      "python",
      "-c",
      "import sys; from tests.market_data.adapters.manual_upload.fixtures import build_full_coverage_workbook; sys.stdout.buffer.write(build_full_coverage_workbook())",
    ],
    {
      cwd: BACKEND_DIR,
      env: { ...process.env, PYTHONPATH: "." },
      maxBuffer: 16 * 1024 * 1024,
      encoding: "buffer",
    },
  );
  return stdout;
}

async function expectDisabledWithReason(
  page: Page,
  name: string | RegExp,
  reason: RegExp,
): Promise<void> {
  const control = page.getByRole("button", { name }).first();
  await expect(control).toBeVisible();
  await expect(control).toBeDisabled();
  const wrapper = control.locator("..");
  await wrapper.focus();
  await expect(wrapper).toBeFocused();
  await expect(wrapper).toHaveAccessibleDescription(reason);
  await expect(page.getByRole("tooltip", { name: reason })).toBeVisible();
}

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: "wait" });
});

test.describe("Markets analyst", () => {
  test.use({
    storageState: path.join(E2E_TMP, "analyst.json"),
    storyConfig: { roles: ["analyst"], bindings: [] },
  });

  test("uploads market data through the Data Engine", async ({ page }) => {
    requireObjectStorage();
    const workbook = await fullCoverageWorkbook();

    await page.goto("/data-engine/market-data");
    await expect(
      page.getByRole("heading", { name: "Manual upload" }),
    ).toBeVisible();

    const templateResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/market-data/templates/yield_curve") &&
        response.request().method() === "GET",
    );
    await page
      .getByRole("button", { name: /Yield curve/i })
      .first()
      .click();
    const template = await templateResponse;
    expect(template.status()).toBe(200);
    expect(new URL(template.url()).searchParams.get("bank_id")).toBe(
      "BK-SAMP0001",
    );

    await page.locator('input[type="file"]').setInputFiles({
      name: "full.xlsx",
      mimeType:
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      buffer: workbook,
    });
    await page.locator('input[type="date"]').fill(UPLOAD_AS_OF);
    const uploadButton = page.getByRole("button", { name: "Upload" });
    await expect(uploadButton).toBeEnabled();
    const uploadResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/market-data/uploads") &&
        response.request().method() === "POST",
    );
    await uploadButton.click();
    const upload = await uploadResponse;
    expect(upload.status()).toBe(200);
    const uploaded = await upload.json();
    expect(uploaded.bank_id).toBe("BK-SAMP0001");
    expect(uploaded.canonical_records_produced).toBeGreaterThan(0);
    await expect(page.getByText(/canonical records across/)).toBeVisible();

    if (evidenceDir) {
      await writeFile(
        path.join(evidenceDir, "markets-analyst-upload.json"),
        JSON.stringify(uploaded, null, 2),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "markets-analyst-upload.png"),
        fullPage: true,
      });
    }
  });

  test("layers a private spread on an uploaded curve", async ({ page }) => {
    await page.goto("/markets");
    await page.getByRole("button", { name: "Curves", exact: true }).click();
    const editSpreads = page
      .getByRole("button", { name: /Edit spreads/ })
      .first();
    await expect(editSpreads).toBeEnabled();
    await editSpreads.click();
    const drawer = page.getByRole("dialog", { name: /Overlay spreads for/ });
    await expect(drawer).toBeVisible();
    await drawer.getByPlaceholder("25").fill("25");
    const saveButton = drawer.getByRole("button", { name: "Save spread" });
    await expect(saveButton).toBeEnabled();
    const overlayResponse = page.waitForResponse(
      (response) =>
        response.url().endsWith("/market-data/overlays") &&
        response.request().method() === "POST",
    );
    await saveButton.click();
    const overlay = await overlayResponse;
    expect(overlay.status()).toBe(201);
    await expect(
      drawer.getByRole("button", { name: "End today" }),
    ).toBeEnabled();

    if (evidenceDir) {
      await writeFile(
        path.join(evidenceDir, "markets-analyst-overlay.json"),
        JSON.stringify(await overlay.json(), null, 2),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "markets-analyst-overlay.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("unbound Markets user", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [{ bundle: "viewer", module: "liq", sensitivity: "all" }],
    },
  });

  test("disables navigation, denies deep links, and sends no Markets requests", async ({
    page,
  }) => {
    const marketRequests: string[] = [];
    page.on("request", (request) => {
      if (MARKET_DATA_REQUEST.test(request.url())) {
        marketRequests.push(request.url());
      }
    });

    // The module stays in the catalogue, disabled with the grant to ask for
    // (permission-only policy); the route itself shows the access-denied page
    // naming that grant.
    await page.goto("/liquidity");
    const marketsEntry = page
      .getByRole("navigation")
      .getByRole("link", { name: "Markets" })
      .first();
    await expect(marketsEntry).toHaveAttribute("aria-disabled", "true");
    await marketsEntry.focus();
    await expect(marketsEntry).toHaveAccessibleDescription(
      /Requires Markets · Published · View/i,
    );
    await page.goto("/markets");
    await expect(
      page.getByRole("heading", { name: "Access required" }),
    ).toBeVisible();
    await expect(
      page.getByText("Markets · Published · View", { exact: true }),
    ).toBeVisible();
    expect(marketRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "markets-unbound.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Markets published reader", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [
        { bundle: "viewer", module: "markets", sensitivity: "published" },
        { bundle: "viewer", module: "data", sensitivity: "all" },
      ],
    },
  });

  test("reads the hub but cannot open spreads, list connections, or upload", async ({
    page,
  }) => {
    await expectBankStatus("/market-data/overlays", 403);
    await expectBankStatus("/market-data/connections", 403);
    const confidentialRequests: string[] = [];
    page.on("request", (request) => {
      if (
        /\/market-data\/(?:overlays|connections)|\/implied-rating\//.test(
          request.url(),
        )
      ) {
        confidentialRequests.push(request.url());
      }
    });

    await page.goto("/markets");
    await expect(page.getByRole("heading", { name: "Markets" })).toBeVisible();
    await page.getByRole("button", { name: "Curves", exact: true }).click();
    await expectDisabledWithReason(
      page,
      /Edit spreads/,
      /Requires Markets · Confidential · View/i,
    );
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "markets-reader-disabled-spreads.png"),
        fullPage: true,
      });
    }

    await page.goto("/data-engine/market-data");
    await expect(
      page.getByTestId("market-data-connections-restricted"),
    ).toHaveText(/Requires Markets · Restricted · View/);
    await expect(
      page.getByRole("button", { name: /Yield curve/i }).first(),
    ).toBeEnabled();
    await expectDisabledWithReason(
      page,
      "Upload",
      /Requires Markets · Published · Create/i,
    );
    expect(confidentialRequests).toEqual([]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "markets-reader-disabled-upload.png"),
        fullPage: true,
      });
    }
  });
});

test.describe("Markets confidential reader", () => {
  test.use({
    storageState: path.join(E2E_TMP, "viewer.json"),
    storyConfig: {
      roles: ["viewer"],
      bindings: [
        { bundle: "viewer", module: "markets", sensitivity: "published" },
        { bundle: "viewer", module: "markets", sensitivity: "confidential" },
      ],
    },
  });

  test("opens the spread editor with save and end disabled", async ({
    page,
  }) => {
    const observed = await biApi(
      page.request,
      "viewer",
      `/banks/${SAMPLE_BANK_ID}/market-data/overlays`,
    );
    expect(observed.status).toBe(200);
    const overlays = MarketDataOverlayListReadFromJSON(observed.body).overlays;
    expect(overlays.length).toBeGreaterThan(0);
    await expectBankStatus(`/market-data/overlays/${overlays[0].id}/end`, 403, {
      method: "POST",
      data: { effective_to: new Date().toISOString().slice(0, 10) },
    });
    await expectBankStatus("/market-data/overlays", 403, {
      method: "POST",
      data: {
        base_ref_kind: "curve",
        base_curve_name: "GHS_GOV",
        adjustment_type: "additive_bps",
        value: "10",
        component_tag: "funding_spread",
        effective_from: UPLOAD_AS_OF,
      },
    });
    await page.goto("/markets");
    await page.getByRole("button", { name: "Curves", exact: true }).click();
    const editSpreads = page
      .getByRole("button", { name: /Edit spreads/ })
      .first();
    await expect(editSpreads).toBeEnabled();
    await editSpreads.click();
    const drawer = page.getByRole("dialog", { name: /Overlay spreads for/ });
    await expect(drawer).toBeVisible();
    await expect(drawer.getByText(/Set by /).first()).toBeVisible();
    await drawer.getByPlaceholder("25").fill("10");
    await expectDisabledWithReason(
      page,
      "Save spread",
      /Requires Markets · Confidential · Create/i,
    );
    await expectDisabledWithReason(
      page,
      "End today",
      /Requires Markets · Confidential · Edit/i,
    );

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "markets-reader-disabled-overlay.png"),
        fullPage: true,
      });
    }
  });
});
