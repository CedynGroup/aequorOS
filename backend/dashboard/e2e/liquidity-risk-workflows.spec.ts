// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Liquidity, worked end to end by an analyst: read the fixture's LCR, buffer
 * and NSFR back off the Cockpit, Buffer and NSFR tabs, read the CFP's
 * early-warning evaluation, then run an enterprise stress from the Stress tab
 * and confirm the stressed coverage it shows is the run the platform kept.
 *
 * The expectations are the FIXTURE's, not the screen's. Every balance below is
 * the canonical book's (`tests/fixtures/canonical_bank_fixture.py`
 * `_DEPOSITS_M`, `_SECURITIES_M`, `_LOAN_EXPOSURES_M`, `_OFF_BALANCE_M`,
 * `_LCR_INFLOWS_M`, `_CAPITAL_COMPONENTS_M`) and every rate is the governed
 * Basel III run-off / ASF / RSF factor the fixture registers; the spec weights
 * and sums them itself, so LCR and NSFR are derived here, not read back. The
 * early-warning values and the stressed path are engine evaluations of the
 * same book and are pinned as such. The book is carried forward unchanged to
 * the latest month end, so none of these figures move with the calendar.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import { expectKpi, expectRow, ghsM, section } from "./support/figures";
import {
  expectPersistedStressRun,
  runEnterpriseStress,
} from "./support/stress";
import { openTab } from "./support/navigation";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

type Line = { label: string; balance: number; ratePct: number };

/** 30-day outflows: balance (GHS millions) × governed run-off rate. */
const OUTFLOWS: Line[] = [
  { label: "Retail Deposits Stable", balance: 700, ratePct: 5 },
  { label: "Retail Deposits Less Stable", balance: 440, ratePct: 10 },
  { label: "Wholesale Operational", balance: 240, ratePct: 25 },
  { label: "Wholesale Non Op Sme", balance: 200, ratePct: 40 },
  { label: "Wholesale Non Op Corporate", balance: 320, ratePct: 100 },
  { label: "Secured Funding L1", balance: 60, ratePct: 0 },
  { label: "Term Borrowings Gt 1Y", balance: 100, ratePct: 0 },
  { label: "Committed Retail", balance: 80, ratePct: 10 },
  { label: "Committed Corporate", balance: 240, ratePct: 30 },
];
const INFLOWS: Line[] = [
  { label: "Retail Loan Repayments", balance: 60, ratePct: 50 },
  { label: "Corporate Sme Repayments", balance: 90, ratePct: 50 },
  { label: "Interbank Maturing", balance: 45, ratePct: 100 },
];
/** Level 1 HQLA, no haircut: the securities book plus the cash it sources. */
const HQLA = [
  { label: "BoG Bills", balance: 260 },
  { label: "GoG Bonds", balance: 360 },
  { label: "Cash Vault Hqla", balance: 45 },
  { label: "BoG Excess Reserves Hqla", balance: 70 },
];
/** Available stable funding: liability balance × ASF factor. */
const ASF: Line[] = [
  // Tier 1 (280, see the IRRBB journey) + Tier 2 (45 sub debt + 15 provisions).
  { label: "Capital Total", balance: 340, ratePct: 100 },
  { label: "Retail Deposits Stable", balance: 700, ratePct: 95 },
  { label: "Retail Deposits Less Stable", balance: 440, ratePct: 90 },
  { label: "Wholesale Operational", balance: 240, ratePct: 50 },
  { label: "Wholesale Non Op Sme", balance: 200, ratePct: 90 },
  { label: "Wholesale Non Op Corporate", balance: 320, ratePct: 50 },
  { label: "Secured Funding L1", balance: 60, ratePct: 0 },
  { label: "Term Borrowings Gt 1Y", balance: 100, ratePct: 100 },
];
/** Required stable funding: asset balance × RSF factor. */
const RSF: Line[] = [
  { label: "Cash Vault", balance: 45, ratePct: 0 },
  { label: "BoG Required Reserves", balance: 175, ratePct: 0 },
  { label: "BoG Excess Reserves", balance: 70, ratePct: 0 },
  { label: "Securities BoG Bills", balance: 260, ratePct: 5 },
  { label: "Securities GoG Bonds", balance: 360, ratePct: 5 },
  { label: "Corporate Unrated", balance: 560, ratePct: 85 },
  { label: "Sme Retail", balance: 280, ratePct: 85 },
  { label: "Retail Other", balance: 250, ratePct: 85 },
  { label: "Residential Mortgage", balance: 200, ratePct: 65 },
  { label: "Commercial Real Estate", balance: 60, ratePct: 85 },
  { label: "Past Due 90", balance: 50, ratePct: 100 },
  // The balance-sheet plug that makes the fixture's assets tie to its funding.
  { label: "Other Assets", balance: 90, ratePct: 100 },
  { label: "Off Balance Commitments", balance: 320, ratePct: 5 },
];

const weighted = (line: Line) => (line.balance * line.ratePct) / 100;
const total = (lines: Line[]) => sum(lines.map(weighted));

const OUTFLOWS_M = total(OUTFLOWS);
const GROSS_INFLOWS_M = total(INFLOWS);
const CAPPED_INFLOWS_M = Math.min(GROSS_INFLOWS_M, 0.75 * OUTFLOWS_M);
const NET_OUTFLOWS_M = OUTFLOWS_M - CAPPED_INFLOWS_M;
const HQLA_M = sum(HQLA.map((h) => h.balance));
const LCR_PCT = (HQLA_M / NET_OUTFLOWS_M) * 100;
const ASF_M = total(ASF);
const RSF_M = total(RSF);
const NSFR_PCT = (ASF_M / RSF_M) * 100;

/** The engine's evaluation of the fixture's canonical position book. */
const EARLY_WARNINGS = [
  { name: "Growing concentration in funding sources", current: "46.21%" },
  { name: "Increase in currency mismatches", current: "2.97%" },
  {
    name: "Decline in weighted-average maturity of liabilities",
    current: "31 days",
  },
  {
    name: "Repeated incidents approaching internal or regulatory limits",
    current: "0",
  },
  { name: "Deterioration in earnings or asset quality", current: "3.54%" },
  { name: "Rising cost of funding", current: "0.07%" },
];

/**
 * The enterprise stress engine's path for the fixture book under the system
 * "Adverse domestic downturn" scenario over three years.
 */
const ADVERSE = {
  name: "Adverse domestic downturn",
  stressedLcrPct: 121.864245,
  stressedCarPct: 16.014852,
  baselineCarPct: 17.422771,
};

test.describe("Liquidity functional workflow", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("reads the fixture's LCR, buffer, NSFR and CFP, then stresses and persists", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    // The derived position: 735 HQLA over 499 net outflows, 1,961 ASF over 1,294.5 RSF.
    expect([HQLA_M, OUTFLOWS_M, CAPPED_INFLOWS_M, NET_OUTFLOWS_M]).toEqual([
      735, 619, 120, 499,
    ]);
    expect([ASF_M, RSF_M]).toEqual([1961, 1294.5]);

    // ---- Cockpit: the ratios, then the outflow and inflow decomposition.
    await page.goto("/liquidity");
    await expect(
      page.getByRole("heading", { name: "Liquidity Cockpit" }),
    ).toBeVisible();
    await expectKpi(page, "Liquidity Coverage Ratio", pct(LCR_PCT, 2));
    await expectKpi(page, "HQLA stock", ghsM(HQLA_M), "Post-haircut weighted");
    await expectKpi(
      page,
      "30-day net outflows",
      ghsM(NET_OUTFLOWS_M),
      "Outflows − capped inflows",
    );
    await expectKpi(page, "Net Stable Funding Ratio", pct(NSFR_PCT, 2));
    await expectKpi(page, "Available stable funding", ghsM(ASF_M));
    await expectKpi(page, "Required stable funding", ghsM(RSF_M));
    await expectKpi(page, "NSFR funding surplus", ghsM(ASF_M - RSF_M));
    await expectKpi(
      page,
      "Largest HQLA concentration",
      pct((360 / HQLA_M) * 100, 1),
      "GoG Bonds",
    );
    await expectKpi(
      page,
      "Early-warning posture",
      "Normal",
      "Business as usual",
    );
    await expectKpi(page, "CFP readiness", "No approved plan");

    const outflows = section(page, "Cash outflows").getByRole("table");
    for (const line of OUTFLOWS) {
      await expectRow(outflows, line.label, lineCells(line));
    }
    await expectRow(outflows, "TOTAL CASH OUTFLOWS", [
      "TOTAL CASH OUTFLOWS",
      ghsM(sum(OUTFLOWS.map((l) => l.balance))),
      "—",
      ghsM(OUTFLOWS_M),
    ]);
    const inflows = section(page, "Cash inflows").getByRole("table");
    for (const line of INFLOWS) {
      await expectRow(inflows, line.label, lineCells(line));
    }
    await expectRow(inflows, "GROSS INFLOWS", [
      "GROSS INFLOWS",
      ghsM(sum(INFLOWS.map((l) => l.balance))),
      "—",
      ghsM(GROSS_INFLOWS_M),
    ]);
    await expectRow(inflows, "CAPPED INFLOWS (min of gross, 75% of outflows)", [
      "CAPPED INFLOWS (min of gross, 75% of outflows)",
      "—",
      "—",
      ghsM(CAPPED_INFLOWS_M),
    ]);

    // ---- Buffer: every Level 1 instrument at face value and its share.
    await openTab(page, "Buffer");
    await expectKpi(page, "HQLA stock", ghsM(HQLA_M));
    await expectKpi(
      page,
      "Coverage of net outflows",
      pct(LCR_PCT, 1),
      "= LCR for the 30-day horizon",
    );
    await expectKpi(
      page,
      "Asset classes held",
      String(HQLA.length),
      "Largest: GoG Bonds",
    );
    const buffer = section(page, "Buffer detail").getByRole("table");
    for (const h of HQLA) {
      await expectRow(buffer, h.label, [
        h.label,
        ghsM(h.balance),
        "0.0%",
        ghsM(h.balance),
        pct((h.balance / HQLA_M) * 100, 1),
      ]);
    }
    await expectRow(buffer, "TOTAL HQLA", [
      "TOTAL HQLA",
      ghsM(HQLA_M),
      "—",
      ghsM(HQLA_M),
      "100.0%",
    ]);

    // ---- NSFR: both sides of the ratio, line by line.
    await openTab(page, "NSFR");
    const asf = section(page, "Available Stable Funding (ASF)").getByRole(
      "table",
    );
    for (const line of ASF) {
      await expectRow(asf, line.label, lineCells(line));
    }
    await expectRow(asf, "TOTAL ASF", ["TOTAL ASF", "—", "—", ghsM(ASF_M)]);
    const rsf = section(page, "Required Stable Funding (RSF)").getByRole(
      "table",
    );
    for (const line of RSF) {
      await expectRow(rsf, line.label, lineCells(line));
    }
    await expectRow(rsf, "TOTAL RSF", ["TOTAL RSF", "—", "—", ghsM(RSF_M)]);
    await expect(
      page.getByText(
        `NSFR = Total ASF ${ghsM(ASF_M)} / Total RSF ${ghsM(RSF_M)} = ${pct(NSFR_PCT, 2)}. ` +
          `Basel minimum 100%.`,
      ),
    ).toBeVisible();

    // ---- CFP: the early-warning evaluation and the plan's state.
    await openTab(page, "CFP");
    await expect(
      page.getByRole("heading", { name: "Contingency Funding Plan" }),
    ).toBeVisible();
    await expectKpi(page, "Escalation state", "Business as usual");
    await expectKpi(page, "Indicators at action", "0", "0 at watch");
    await expectKpi(
      page,
      "Board-approved CFP",
      "None",
      "Approve a plan before activation is possible",
    );
    await expectKpi(page, "CFP activation", "Not active");
    const indicators = section(page, "Early-warning indicators").getByRole(
      "table",
    );
    for (const ewi of EARLY_WARNINGS) {
      const row = indicators.locator("tbody tr").filter({ hasText: ewi.name });
      await expect(row).toHaveCount(1);
      await expect(row.locator("td").nth(1)).toHaveText(ewi.current);
      // No Board trigger levels are registered, so no classification is invented.
      await expect(row.locator("td").nth(3)).toHaveText("Not set");
      await expect(row.locator("td").nth(4)).toHaveText("Not set");
      await expect(row.locator("td").nth(5)).toHaveText("Unconfigured");
    }

    // ---- Stress: drive the adverse scenario through every engine.
    await openTab(page, "Stress");
    await expect(
      page.getByRole("heading", { name: "Enterprise Stress Workbench" }),
    ).toBeVisible();
    const run = await runEnterpriseStress(page, `${ADVERSE.name} (moderate)`);
    expect(Number(run.summary.baseline_lcr_pct)).toBeCloseTo(LCR_PCT, 5);

    await expectKpi(
      page,
      "Stressed LCR",
      pct(ADVERSE.stressedLcrPct, 1),
      `Base ${pct(LCR_PCT, 1)} · floor 100%`,
    );
    await expectKpi(
      page,
      "Stressed CAR",
      pct(ADVERSE.stressedCarPct, 2),
      `Base ${pct(ADVERSE.baselineCarPct, 2)}`,
    );
    await expectKpi(page, "Solvency × liquidity", "Both hold");

    // The platform kept that run as the latest for this date and scenario,
    // and a fresh page re-opens it from the run registry.
    await expectPersistedStressRun(page, run, pct(ADVERSE.stressedCarPct, 2));

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "liquidity-stress.png"),
        fullPage: true,
      });
    }
  });
});

function lineCells(line: Line): string[] {
  return [
    line.label,
    ghsM(line.balance),
    `${line.ratePct}%`,
    ghsM(weighted(line)),
  ];
}

function pct(value: number, decimals: number): string {
  return `${value.toFixed(decimals)}%`;
}

function sum(values: number[]): number {
  return values.reduce((t, v) => t + v, 0);
}
