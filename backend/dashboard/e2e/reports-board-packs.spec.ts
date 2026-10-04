/**
 * Board and regulator reporting: the board pack and the stress board pack,
 * composed for the bank's reporting date and printed to PDF.
 *
 * Each journey reads its key figures from the surfaces that own them — the
 * module cockpits, the enterprise stress workbench, the official-runs
 * registry — and then requires the composed pack AND the PDF the reader saves
 * to restate exactly those figures. A pack that drifted from the app, lost a
 * section in print, or printed its own controls fails here.
 */

import { expect, test, type Locator, type Page } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { printToPdf } from "./support/print";

/** The value span of the KPI tile whose label is exactly `label`. */
function kpiValue(scope: Page | Locator, label: string): Locator {
  return scope
    .getByText(label, { exact: true })
    .locator(
      "xpath=ancestor::div[contains(concat(' ', normalize-space(@class), ' '), ' card ')][1]",
    )
    .locator("span.font-mono")
    .first();
}

/** The value cell of the board-pack metric row whose label (before its hint) is `label`. */
function metricValue(scope: Locator, label: string): Locator {
  const escaped = label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return scope
    .getByRole("row")
    .filter({ hasText: new RegExp(`^${escaped}`) })
    .getByRole("cell")
    .nth(1);
}

/**
 * A rendered figure, whitespace-normalised as the PDF's extracted text is —
 * currency formatting separates the code from the amount with a no-break space.
 */
async function text(locator: Locator): Promise<string> {
  await expect(locator).toHaveText(/\S/);
  return (await locator.innerText()).replace(/\s+/g, " ").trim();
}

test.describe("board and regulator reporting", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("board pack restates the module figures and prints them to PDF", async ({
    page,
  }) => {
    await page.goto("/liquidity");
    const hqla = await text(kpiValue(page, "HQLA stock"));
    const outflows = await text(kpiValue(page, "30-day net outflows"));

    await page.goto("/basel");
    const car = `${await text(kpiValue(page, "Capital Adequacy Ratio"))}%`;

    const periodsLoaded = page.waitForResponse(
      (response) =>
        /\/reporting-periods$/.test(new URL(response.url()).pathname) &&
        response.ok(),
    );
    await page.goto("/reports");
    const { periods } = (await (await periodsLoaded).json()) as {
      periods: { label: string }[];
    };
    const reportingPeriod = periods[0].label;
    await page.getByRole("link", { name: "Open board pack" }).click();

    await expect(page).toHaveURL(/\/reports\/board-pack$/);
    await expect(
      page.getByRole("heading", { name: "Board Pack", exact: true }),
    ).toBeVisible();
    const headerAsOf = (await text(page.getByText(/^As of /).first())).replace(
      /^As of /,
      "",
    );

    const cover = page
      .locator(".bp-page")
      .filter({ hasText: "Board Risk & Regulatory Pack" });
    await expect(cover).toContainText("Sample Bank Ltd");
    await expect(cover).toContainText(`${reportingPeriod} · ${headerAsOf}`);
    await expect(cover).toContainText("Current with the latest ingested data");

    const summary = page.locator(".bp-page").filter({
      has: page.getByRole("heading", { name: "Executive summary" }),
    });
    const liquidity = page.locator(".bp-page").filter({
      has: page.getByRole("heading", { name: /^Liquidity/ }),
    });
    const capital = page.locator(".bp-page").filter({
      has: page.getByRole("heading", { name: /^Basel Capital/ }),
    });
    const briefs = [
      "Liquidity",
      "Basel Capital",
      "Credit / Loan Book",
      "Interest Rate Risk (IRRBB)",
      "FX Risk",
      "Funds Transfer Pricing",
    ];
    // Each brief loads its own module; the pack is complete once all six are in.
    for (const brief of briefs) {
      await expect(
        page.getByRole("heading", {
          level: 3,
          name: new RegExp(`^${brief.replace(/[()/]/g, "\\$&")}`),
        }),
      ).toBeVisible();
    }

    const lcr = await text(metricValue(liquidity, "Liquidity Coverage Ratio"));
    await expect(kpiValue(summary, "Liquidity")).toHaveText(lcr);
    await expect(
      metricValue(liquidity, "High-quality liquid assets"),
    ).toHaveText(hqla);
    await expect(metricValue(liquidity, "Net outflows (30 days)")).toHaveText(
      outflows,
    );
    await expect(metricValue(capital, "Capital Adequacy Ratio")).toHaveText(
      car,
    );
    await expect(kpiValue(summary, "Capital")).toHaveText(car);

    // The liquidity brief cites the official run minted for this book; the
    // registry must hold that same immutable run.
    await expect(liquidity).toContainText("Official run provenance");
    const runBadge = liquidity.locator(
      "[title^='Run '][title*=' · input hash ']",
    );
    const provenance = await runBadge.getAttribute("title");
    const [, inputHash] = / · input hash (\S+)$/.exec(provenance ?? "") ?? [];
    expect(inputHash).toMatch(/^[0-9a-f]{16,}$/);
    // The badge reads "<engine> · <input hash> · <minted at>".
    const [engine] = (await text(runBadge)).split(" · ");
    expect(engine).toMatch(/^regulatory-liquidity-v/);

    const pdf = await printToPdf(
      page,
      page.getByRole("button", { name: "Print / Save as PDF" }),
    );
    // Cover, executive summary and six module briefs, one A4 page each.
    expect(pdf.pages.length).toBeGreaterThanOrEqual(8);
    expect(pdf.pages[0]).toContain("Board Risk & Regulatory Pack");
    expect(pdf.pages[0]).toContain("Sample Bank Ltd");
    expect(pdf.pages[0]).toContain(`${reportingPeriod} · ${headerAsOf}`);
    expect(pdf.pages[1]).toContain("Executive summary");
    for (const figure of [lcr, car, hqla, outflows, engine]) {
      expect(pdf.text).toContain(figure);
    }
    expect(pdf.text).toContain(inputHash.slice(0, 8));
    for (const brief of briefs) {
      expect(pdf.text).toContain(brief);
    }
    expect(pdf.text).not.toContain("Print / Save as PDF");
    expect(pdf.text).not.toContain("Reports library");

    await page.getByRole("link", { name: "Reports library" }).click();
    await expect(page).toHaveURL(/\/reports$/);
    const registeredRun = page
      .getByRole("row")
      .filter({ hasText: inputHash.slice(0, 10) });
    await expect(registeredRun).toHaveCount(1);
    await expect(registeredRun).toContainText("Liquidity");
    await expect(registeredRun).toContainText(engine);
    await expect(registeredRun).toContainText("Succeeded");
  });

  test("stress board pack composes a persisted stress run and prints it to PDF", async ({
    page,
  }) => {
    await page.goto("/liquidity/stress");
    await expect(page.getByLabel("Approved scenario")).not.toHaveValue("");
    const ran = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        /\/enterprise-stress\/runs$/.test(new URL(response.url()).pathname),
    );
    await page
      .getByRole("button", { name: "Run enterprise stress", exact: true })
      .click();
    const response = await ran;
    expect(response.ok()).toBe(true);
    const run = (await response.json()) as {
      run_id: string;
      scenario_code: string;
      input_hash: string;
    };
    const stressedCar = await text(kpiValue(page, "Stressed CAR"));
    const carErosion = await text(kpiValue(page, "CAR erosion"));

    await page.getByRole("link", { name: "Board-pack composer" }).click();
    await expect(page).toHaveURL(/\/reports\/stress-board-pack$/);
    await expect(
      page.getByRole("heading", { name: "Stress Board-Pack Composer" }),
    ).toBeVisible();

    // The select's accessible name carries its selected option after "Run".
    const runSelect = page.getByRole("combobox", { name: /^Run\b/ });
    await runSelect.selectOption(run.run_id);
    const coverTitle = `ICAAP Stress Test — ${run.scenario_code}`;
    await expect(page.getByText(coverTitle, { exact: true })).toBeVisible();
    await expect(kpiValue(page, "Stressed CAR")).toHaveText(stressedCar);
    await expect(kpiValue(page, "CAR erosion")).toHaveText(carErosion);
    const provenance = `Immutable run ${run.run_id.slice(0, 10)} · input hash ${run.input_hash.slice(0, 12)}`;
    await expect(page.getByText(provenance)).toBeVisible();

    const runTag = run.run_id.slice(0, 8);
    const analystNote = `Analyst view ${runTag}: capital holds above the floor.`;
    const croNote = `CRO challenge ${runTag}: confirm the funding assumptions.`;
    await page.getByLabel("Analyst commentary").fill(analystNote);
    await page.getByLabel("CRO / board challenge").fill(croNote);
    const narrative = page
      .locator(".card")
      .filter({ hasText: "Narrative & assumptions rationale" });
    await expect(narrative).toContainText(analystNote);
    await expect(narrative).toContainText(croNote);

    await expect(page.getByText("Table 1 — Summary Results")).toBeVisible();
    await page.getByLabel("Appendix II").uncheck();
    await expect(page.getByText("Table 1 — Summary Results")).toHaveCount(0);

    const pdf = await printToPdf(
      page,
      page.getByRole("button", { name: "Print / export PDF" }),
    );
    expect(pdf.pages.length).toBeGreaterThanOrEqual(1);
    for (const content of [
      coverTitle,
      stressedCar,
      carErosion,
      provenance,
      analystNote,
      croNote,
    ]) {
      expect(pdf.text).toContain(content);
    }
    expect(pdf.text).not.toContain("Print / export PDF");
    expect(pdf.text).not.toContain("Pick a run and the sections to include");
    expect(pdf.text).not.toContain("Table 1 — Summary Results");

    // The run is immutable and persisted: a fresh composer composes it again
    // with the same figures.
    await page.reload();
    await runSelect.selectOption(run.run_id);
    await expect(page.getByText(coverTitle, { exact: true })).toBeVisible();
    await expect(kpiValue(page, "Stressed CAR")).toHaveText(stressedCar);
    await expect(page.getByText(provenance)).toBeVisible();
  });
});
