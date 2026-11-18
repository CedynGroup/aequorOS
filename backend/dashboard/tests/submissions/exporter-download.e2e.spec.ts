/** The read-only exporter downloads a finished return through the workspace.
 *
 * A viewer-scoped actor whose only authority is one exact-bank Exporter
 * binding (Regulatory Reporting / restricted) opens the returns workspace and
 * takes the finished return's sealed file away — and is offered none of the
 * preparation, approval or transmission controls, because the server projects
 * exactly view and export for them.
 */

import { expect, type Page } from "@playwright/test";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { test } from "../../e2e/support/story-identities";
import { E2E_API_ORIGIN, E2E_TMP } from "../../playwright.config";
import { mintBackendToken } from "../../e2e/support/mint";
import { returnsUrl } from "../../e2e/support/ceremony";
import { requireObjectStorage } from "../../e2e/support/object-storage";

const SAMPLE_BANK_ID = "BK-SAMP0001";
/** The platform's liquidity return — the one global-setup mints a run for. */
const LIQUIDITY_RETURN = "LCR-NSFR";
// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const viewerState = path.join(E2E_TMP, "viewer.json");

test.use({
  storyConfig: {
    roles: ["viewer"],
    bindings: [
      { role: "viewer", bundle: "exporter", module: "reg", sensitivity: "restricted" },
    ],
  },
  storageState: viewerState,
});

let reportingDate: string;

test.beforeAll(async ({ request }) => {
  requireObjectStorage();
  const admin = `Bearer ${await mintBackendToken("admin")}`;
  const listing = await request.get(
    `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    { headers: { Authorization: admin } },
  );
  expect(listing.ok()).toBeTruthy();
  const periods = (await listing.json()) as {
    periods: { id: string; period_end: string }[];
  };
  // Journeys claim distinct periods against the shared canonical bank:
  // global setup and the submission lifecycle use [0]; the full lifecycle's
  // five journeys use [1..5] and its BoG form [6]. The seventh is this one's.
  const period = periods.periods[7];
  reportingDate = String(period.period_end).slice(0, 10);
  // LCR-NSFR generation pulls from the latest succeeded liquidity baseline
  // run of its reporting period — mint one for the claimed period.
  const run = await request.post(
    `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}/regulatory-runs`,
    {
      headers: { Authorization: admin },
      data: {
        module: "liquidity",
        reporting_period_id: period.id,
        scenario_code: "baseline",
      },
    },
  );
  expect(run.ok()).toBeTruthy();
  // Finish the return administratively: the exporter's whole premise is that
  // the preparer's work already exists, so no one on the page generates it.
  const generated = await request.post(
    `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}/regulatory-packages`,
    {
      headers: { Authorization: admin },
      data: { return_code: LIQUIDITY_RETURN, reporting_date: reportingDate },
    },
  );
  expect(generated.ok()).toBeTruthy();
});

/** The exporter-only actor's page; the story fixture wrote their session state. */
async function exporterPage(page: Page): Promise<Page> {
  await page.goto(returnsUrl(LIQUIDITY_RETURN, reportingDate));
  await expect(
    page.getByRole("heading", { name: /returns workspace/i }),
  ).toBeVisible();
  return page;
}

test("an exporter reads the finished return and downloads its sealed file", async ({
  page,
}) => {
  await exporterPage(page);

  // Preparation is not this role's: no generate control is offered at all.
  await expect(
    page.getByRole("button", { name: /generate the return|^regenerate$/i }),
  ).toHaveCount(0);

  // The export surface is: the finished return's command bar serves the
  // sealed values-only Excel through the export and download routes.
  const served = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      /\/regulatory-artifacts\/[^/]+\/download$/.test(new URL(response.url()).pathname),
    { timeout: 60_000 },
  );
  const saved = page.waitForEvent("download", { timeout: 60_000 });
  await page
    .getByTestId("return-command-bar")
    .getByRole("button", { name: /xlsx/i })
    .click();
  const response = await served;
  expect(response.ok()).toBeTruthy();
  const download = await saved;
  const bytes = await readFile(await download.path());
  // A sealed values-only workbook is a real xlsx: a zip container, non-empty.
  expect(bytes.length).toBeGreaterThan(1000);
  expect(bytes.subarray(0, 2).toString("latin1")).toBe("PK");

  if (evidenceDir) {
    await page.screenshot({
      path: path.join(evidenceDir, "exporter-download-finished-return.png"),
      fullPage: true,
    });
  }
});

test("an exporter is offered no review, approval or transmission control", async ({
  page,
}) => {
  await exporterPage(page);
  for (const name of [/^validate$/i, /^re-run checks$/i, /^approve/i, /^submit/i]) {
    await expect(page.getByRole("button", { name })).toHaveCount(0);
  }
  // The chain describes the return without offering the acts.
  await expect(page.getByTestId("filing-chain")).toBeVisible();
});
