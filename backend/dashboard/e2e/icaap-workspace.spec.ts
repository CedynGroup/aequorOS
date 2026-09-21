/**
 * The ICAAP workspace, end to end, on the hermetic stack.
 *
 * The journey is the P1 acceptance walk: create a REHEARSAL cycle (D-029 — the
 * full lifecycle, never a filing), write a section, bind a set of figures, cite
 * one of them inside a sentence, commit a version, attach a document, and take
 * both draft exports.
 *
 * Bootstrap needs no change: `scripts/e2e_bootstrap.py` already grants the
 * `analyst` fixture an organization-wide ANALYST binding over every module and
 * sensitivity, which includes CAP/confidential view, create, edit and export.
 * Adding a user or a binding there would shift the pinned `authv` values in
 * `support/mint.ts`, so it is deliberately left alone.
 */

import { expect, test } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { mintBackendToken } from "./support/mint";
import { requireObjectStorage } from "./support/object-storage";

const SAMPLE_BANK_ID = "BK-SAMP0001";

/**
 * The financial year the canonical fixture can actually compute.
 *
 * A cycle's figures bind to the book AS AT the framework's reporting date —
 * there is no "nearest earlier period" fallback, by design — so the journey
 * must choose the year whose 31 December the fixture carries. Resolved from
 * the API in `beforeAll` rather than written down, so a fixture that moves
 * does not silently leave this test binding nothing.
 */
let fixtureFiscalYear = 0;

async function api(
  token: string,
  method: string,
  pathName: string,
  body?: unknown,
): Promise<any> {
  const response = await fetch(`${E2E_API_ORIGIN}/api/v1${pathName}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    throw new Error(
      `${method} ${pathName} -> ${response.status}: ${await response.text()}`,
    );
  }
  return response.json();
}

test.describe("ICAAP workspace", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });
  test.describe.configure({ mode: "serial" });

  test.beforeAll(async () => {
    // A cycle's figures bind to a SEALED capital run for the exact reporting
    // date, never to the live plane — so the run has to exist before the
    // journey asks for it.
    const admin = await mintBackendToken("admin");
    const periods = await api(
      admin,
      "GET",
      `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    );
    const yearEnd =
      periods.periods.find((period: { period_end: string }) =>
        period.period_end.endsWith("-12-31"),
      ) ?? periods.periods[0];
    await api(admin, "POST", `/banks/${SAMPLE_BANK_ID}/regulatory-runs`, {
      module: "capital",
      reporting_period_id: yearEnd.id,
      scenario_code: "baseline",
    });
    fixtureFiscalYear = Number(String(yearEnd.period_end).slice(0, 4));
    expect(fixtureFiscalYear).toBeGreaterThan(2000);
  });

  test("creates a rehearsal cycle, writes a section and commits a version", async ({
    page,
  }) => {
    await page.goto("/icaap");
    await expect(
      page.getByRole("heading", {
        name: /Internal Capital Adequacy Assessment/i,
      }),
    ).toBeVisible();

    await page.getByRole("button", { name: "New cycle" }).click();
    const dialog = page.getByRole("dialog", { name: "New ICAAP cycle" });
    await expect(dialog).toBeVisible();
    // The rehearsal label is the point of D-029, so it is asserted, not assumed.
    await expect(dialog.getByText(/not a regulatory filing/i)).toBeVisible();
    await dialog.getByRole("radio", { name: /Rehearsal/i }).check();
    await dialog
      .getByLabel("Financial year", { exact: true })
      .selectOption(String(fixtureFiscalYear));
    await dialog.getByLabel("Reason", { exact: true }).fill("P1 acceptance walk-through");
    await dialog.getByRole("button", { name: "Create cycle" }).click();

    await expect(page).toHaveURL(/\/icaap\/[0-9a-f-]+\/overview$/);
    await expect(page.getByText(/Rehearsal cycle/i).first()).toBeVisible();

    // Sections: open the first one and write in it.
    await page.getByRole("link", { name: "Sections", exact: true }).click();
    await expect(page).toHaveURL(/\/icaap\/[0-9a-f-]+\/sections$/);
    await page.getByRole("link").filter({ hasText: /\(a\)/ }).first().click();
    await expect(page).toHaveURL(/\/sections\/[a-z0-9_]+$/);

    const editor = page.getByLabel("Section narrative");
    await expect(editor).toBeVisible({ timeout: 30_000 });
    await editor.click();
    await editor.type(
      "The board has assessed the capital required to support the plan.",
    );
    await expect(page.getByText("Saved")).toBeVisible({ timeout: 15_000 });

    // Bind a set of figures and cite one of them in the sentence.
    const figures = page.locator("section", { hasText: "Figures" }).first();
    await figures.getByRole("button", { name: "Add", exact: true }).click();
    const addDialog = page.getByRole("dialog", { name: "Add figures" });
    await expect(addDialog).toBeVisible();
    await addDialog
      .locator("select")
      .selectOption({ label: "Capital position (expected here)" });
    await addDialog.getByRole("button", { name: "Add", exact: true }).click();
    await expect(addDialog).toBeHidden();

    // The block must actually appear in the panel AND be bound to real
    // figures. Both halves matter: a contract field arriving `undefined`
    // rather than `null` once filtered every live block out of this list, and
    // a cycle whose reporting date the book does not cover binds nothing.
    await expect(figures.getByText("Capital position")).toBeVisible({
      timeout: 15_000,
    });
    await expect(
      figures.getByRole("button", { name: /Capital position Up to date/i }),
    ).toBeVisible({ timeout: 15_000 });

    // Expand the card (the only control that reports an expanded state) and
    // cite a figure inside the sentence.
    await figures.locator("button[aria-expanded]").first().click();
    const cite = figures.getByRole("button", { name: /^Cite /i }).first();
    await expect(cite).toBeVisible({ timeout: 15_000 });
    await cite.click();

    await page.getByLabel("Commit note").fill("First draft of the summary");
    await page.getByRole("button", { name: "Commit version" }).click();
    await expect(page.getByText(/Version 1/)).toBeVisible({ timeout: 15_000 });
  });

  test("downloads the draft PDF and the Word working copy", async ({ page }) => {
    await page.goto("/icaap");
    // The cycle's title is the framework's own wording, so the row is reached
    // by position rather than by a title this test would have to duplicate.
    await page.locator("table a").first().click();
    await expect(page).toHaveURL(/\/icaap\/[0-9a-f-]+\//);

    for (const [label, extension] of [
      ["Draft PDF", ".pdf"],
      ["Draft Word (working copy)", ".docx"],
    ] as const) {
      const download = page.waitForEvent("download");
      await page.getByRole("button", { name: label }).click();
      const file = await download;
      expect(file.suggestedFilename().toLowerCase()).toContain(extension);
    }
  });

  test("attaches a supporting document", async ({ page }) => {
    // Attachments are persisted to object storage; without it the upload can
    // never succeed, so refuse immediately rather than burning the timeout.
    requireObjectStorage();

    await page.goto("/icaap");
    await page.locator("table a").first().click();
    await page.getByRole("link", { name: "Attachments", exact: true }).click();
    await expect(page).toHaveURL(/\/attachments$/);

    await page.getByRole("button", { name: "Attach" }).click();
    const dialog = page.getByRole("dialog", { name: "Attach a document" });
    await dialog.getByLabel("Title", { exact: true }).fill("Senior management report");
    await dialog.getByLabel("File", { exact: true }).setInputFiles({
      name: "senior-management-report.pdf",
      mimeType: "application/pdf",
      buffer: Buffer.from(
        "%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n",
      ),
    });
    await dialog.getByRole("button", { name: "Upload" }).click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });
    await expect(
      page.getByText("Senior management report").first(),
    ).toBeVisible();
  });
});

test.describe("a user without Capital confidential authority", () => {
  test.use({ storageState: path.join(E2E_TMP, "liquidity_viewer.json") });

  test("sees no ICAAP entry, 404s the deep link, and sends no ICAAP request", async ({
    page,
  }) => {
    const icaapRequests: string[] = [];
    page.on("request", (request) => {
      if (/\/icaap(\/|$)/.test(new URL(request.url()).pathname)) {
        icaapRequests.push(request.url());
      }
    });

    await page.goto("/");
    await expect(page.getByRole("link", { name: "ICAAP" })).toHaveCount(0);

    await page.goto("/icaap");
    await expect(page.getByText(/404|not found/i).first()).toBeVisible();
    expect(
      icaapRequests.filter((url) => url.includes("/api/v1/")),
    ).toEqual([]);
  });
});
