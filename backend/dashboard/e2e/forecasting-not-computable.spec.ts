// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Forecasting on a fresh institution: no financial book and no approved
 * forecast assumptions. Every Forecasting tool must show its documented
 * not-computable state, and every run the analyst attempts must be refused by
 * the server with its named reason. Nothing may substitute an unapproved
 * assumption or estimate around missing facts.
 *
 * The institution is a directory-only fixture in the disposable database: a
 * Nigerian-jurisdiction bank in the demo organization. The approved forecast
 * presets are effective-dated per jurisdiction, and the fixture approves Ghana's
 * only, which is exactly the provisioned-tenant state #342 describes. The bank
 * starts with no reporting period. A period with no facts is then added, so
 * that the run controls open and the refusals are the server's.
 * `/banks` is narrowed to this one bank (the `icaap-sdi.spec.ts` pattern),
 * because the shell opens the first institution it lists. Every Forecasting
 * request still goes to the real API.
 */
import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { apiGet, section } from "./support/figures";
import { openTab } from "./support/navigation";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;
const FRESH_BANK_ID = "BK-FRSH0001";
const PERIOD_ID = randomUUID();
const MISSING_ASSUMPTIONS =
  "The 'base' forecast scenario parameters do not cover: loan_growth_pct, deposit_growth_pct, nim_pct, cost_to_income_pct, credit_loss_rate_pct, fx_depreciation_pct, dividend_payout_pct.";
const MISSING_FACTS = "The reporting period has no financial facts to analyze.";

/** Create, extend or remove the fresh institution in the disposable database. */
function fixture(action: "create" | "period" | "remove") {
  execFileSync(path.join(__dirname, "../../.venv/bin/python"), [
    "-c",
    `
import sqlite3, sys
db_path, action, bank, period = sys.argv[1:5]
with sqlite3.connect(db_path) as db:
    if action == "create":
        db.execute("INSERT INTO banks SELECT ?, organization_id, 'Tano Fresh Bank', 'Tano', 'NGN', 'NG', license_type, institution_type, NULL, created_at, updated_at FROM banks WHERE id='BK-SAMP0001'", (bank,))
    elif action == "period":
        db.execute("INSERT INTO bank_reporting_periods VALUES ('OR-DEM00001', ?, '2026-09-01', '2026-09-30', '2026-09', 'open', ?, datetime('now'), datetime('now'))", (bank, period.replace('-', '')))
    else:
        runs = [row[0] for row in db.execute("SELECT id FROM regulatory_runs WHERE bank_id=?", (bank,))]
        tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if "run_id" in columns and runs:
                db.execute(f"DELETE FROM {table} WHERE run_id IN ({','.join('?' * len(runs))})", runs)
        for table in tables:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
            if "bank_id" in columns and table != "banks":
                db.execute(f"DELETE FROM {table} WHERE bank_id=?", (bank,))
        db.execute("DELETE FROM banks WHERE id=?", (bank,))
`,
    path.join(E2E_TMP, "e2e.db"),
    action,
    FRESH_BANK_ID,
    PERIOD_ID,
  ]);
}

test.describe("Forecasting on a fresh institution", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test.beforeEach(async ({ page }) => {
    fixture("create");
    await page.route("**/api/v1/banks", async (route) => {
      const response = await route.fetch();
      const payload = await response.json();
      payload.banks = payload.banks.filter(
        (bank: { id: string }) => bank.id === FRESH_BANK_ID,
      );
      // A navigation can cancel the request while it is in flight.
      await route.fulfill({ response, json: payload }).catch(() => undefined);
    });
  });

  test.afterEach(async ({ page }) => {
    await page.unrouteAll({ behavior: "ignoreErrors" });
    fixture("remove");
  });

  test("shows the not-computable state everywhere and the server refuses every run", async ({
    page,
  }) => {
    test.setTimeout(150_000);

    // ---- No book at all: no period, no live baseline, no saved results.
    await page.goto("/forecasting");
    await expect(
      page.getByText("A reporting period is required to run a forecast"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run forecast" }),
    ).toBeDisabled();
    await expect(
      page.getByText("Live forecast is not available yet"),
    ).toBeVisible();
    await openTab(page, "NII Forecast");
    await expect(
      page.getByText("No succeeded forecast runs yet"),
    ).toBeVisible();
    await openTab(page, "Assumptions");
    await expect(
      page.getByText("No succeeded forecast runs yet"),
    ).toBeVisible();
    // No approved preset for this jurisdiction: the catalogue offers none, only
    // the three documented engine defaults, which no preset is built from.
    await expect(
      section(page, "Preset catalogue").getByRole("columnheader"),
    ).toHaveText(["Assumption", "Engine default"]);
    await openTab(page, "Reverse Stress");
    await expect(
      page.getByText("No reverse-stress frontier yet"),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Run reverse stress" }).first(),
    ).toBeDisabled();

    // ---- A period with no facts: the controls open; the server refuses.
    fixture("period");
    await page.goto("/forecasting");
    await expect(
      page.getByText("A reporting period is required to run a forecast"),
    ).toHaveCount(0);
    const forecast = await refusedRun(page, "/forecast/runs", () =>
      page.getByRole("button", { name: "Run forecast" }).click(),
    );
    expect(forecast.assumptions).toBeNull();
    expect(forecast.path).toEqual([]);
    await expect(page.getByText("Run failed")).toBeVisible();
    await expect(page.getByText(MISSING_ASSUMPTIONS)).toBeVisible();

    await openTab(page, "What-if Lab");
    const whatif = await refusedRun(page, "/forecast/whatif", () =>
      page
        .getByRole("button", { name: "Run Interest rate shock +400bps" })
        .click(),
    );
    expect(whatif.shocked_path).toEqual([]);
    const shockAlert = page
      .getByRole("alert")
      .filter({ hasText: "This shock could not be projected" });
    await expect(shockAlert).toBeVisible();
    await expect(shockAlert).toContainText(MISSING_ASSUMPTIONS);
    await expect(shockAlert).toContainText(
      "Engine diagnostic missing_parameter",
    );

    await openTab(page, "Optimizer");
    const search = await refusedRun(page, "/forecast/optimizer", () =>
      page.getByRole("button", { name: "Run optimizer" }).first().click(),
    );
    expect(search.candidates_evaluated).toBe(0);
    const searchAlert = page
      .getByRole("alert")
      .filter({ hasText: "The optimizer could not search this period" });
    await expect(searchAlert).toBeVisible();
    await expect(searchAlert).toContainText(MISSING_ASSUMPTIONS);
    await expect(
      page.getByText("No feasible strategy in this search"),
    ).toHaveCount(0);

    await openTab(page, "Reverse Stress");
    const frontier = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/banks/${FRESH_BANK_ID}/reverse-stress/runs`) &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Run reverse stress" })
      .first()
      .click();
    const refusal = await frontier;
    expect(refusal.status()).toBe(409);
    expect((await refusal.json()).error.details).toEqual({
      error_code: "financial_facts_missing",
      message: MISSING_FACTS,
    });
    await expect(page.getByText("Reverse-stress search failed")).toBeVisible();
    await expect(page.getByText(MISSING_FACTS)).toBeVisible();
    await expect(
      page.getByText("No reverse-stress frontier yet"),
    ).toBeVisible();

    // Nothing was computed: no live result, and every saved run is a failure.
    const live = await apiGet(
      page,
      "analyst",
      `/banks/${FRESH_BANK_ID}/live-summary`,
    );
    expect(live.modules).toEqual([]);
    const runs = await apiGet(
      page,
      "analyst",
      `/banks/${FRESH_BANK_ID}/regulatory-runs`,
    );
    expect(
      runs.runs
        .map((r: { module: string; status: string }) => [r.module, r.status])
        .sort(),
    ).toEqual([
      ["forecast", "failed"],
      ["optimizer", "failed"],
      ["whatif", "failed"],
    ]);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-not-computable.png"),
        fullPage: true,
      });
    }
  });
});

/**
 * Trigger a Forecasting run and return its body: persisted (201) as a FAILED
 * run whose named reason is the missing approved assumptions.
 */
async function refusedRun(
  page: Page,
  pathName: string,
  trigger: () => Promise<void>,
) {
  const ran = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/banks/${FRESH_BANK_ID}${pathName}`) &&
      r.request().method() === "POST",
  );
  await trigger();
  const response = await ran;
  expect(response.status()).toBe(201);
  const body = await response.json();
  expect(body.status).toBe("failed");
  expect(body.error).toMatchObject({
    code: "missing_parameter",
    message: MISSING_ASSUMPTIONS,
  });
  return body;
}
