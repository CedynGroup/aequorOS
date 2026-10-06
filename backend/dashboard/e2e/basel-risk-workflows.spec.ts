// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Basel capital, worked end to end by an analyst: read the fixture's capital
 * ratios, RWA build-up and capital structure back off the Basel tabs, drive an
 * enterprise stress from the Stress tab, then plan: move the what-if planner
 * and refresh the ILAAP evidence, and confirm both the stress run and the ILAAP
 * snapshot are what the platform kept.
 *
 * The expectations are the FIXTURE's, not the screen's. Capital is the
 * canonical book's components (`tests/fixtures/canonical_bank_fixture.py`
 * `_CAPITAL_COMPONENTS_M`); credit RWA is its exposures at their governed
 * standardised risk weights, with committed lines converted at a 50% CCF;
 * market RWA is the FX net open position (45M, see the FX journey) at an 8%
 * charge × 12.5; operational RWA is the Basic Indicator Approach, 15% of the
 * three-year average gross income × 12.5. The spec sums and divides them
 * itself, and recomputes the what-if planner's pro-forma ratios. Only the
 * leverage exposure measure and the stressed path are pinned as engine output.
 * The book is carried forward unchanged to the latest month end, so none of
 * these figures move with the calendar.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_TMP } from "../playwright.config";
import {
  SAMPLE_BANK_ID,
  apiGet,
  expectKpi,
  expectRow,
  ghsM,
  section,
} from "./support/figures";
import {
  expectPersistedStressRun,
  runEnterpriseStress,
} from "./support/stress";
import { openTab } from "./support/navigation";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** Capital components, GHS millions. */
const CET1_ITEMS = [
  { label: "Paid Up Capital", amount: 150 },
  { label: "Retained Earnings", amount: 95 },
  { label: "Statutory Reserves", amount: 45 },
  { label: "Other Reserves", amount: 10 },
];
const CET1_DEDUCTIONS = [
  { label: "Intangibles (Deduction)", amount: 25 },
  { label: "Deferred Tax Assets (Deduction)", amount: 15 },
];
const AT1_M = 20;
const TIER2_M = 45 + 15; // subordinated debt + general provisions (within the cap)
const CET1_M =
  sum(CET1_ITEMS.map((i) => i.amount)) -
  sum(CET1_DEDUCTIONS.map((d) => d.amount));
const TIER1_M = CET1_M + AT1_M;
const TOTAL_CAPITAL_M = TIER1_M + TIER2_M;

/** Credit exposures (GHS millions) at their governed standardised risk weight. */
const CREDIT = [
  { label: "Corporate Unrated", exposure: 560, rw: 100 },
  { label: "Sme Retail", exposure: 280, rw: 75 },
  { label: "Retail Other", exposure: 250, rw: 75 },
  { label: "Residential Mortgage", exposure: 200, rw: 35 },
  { label: "Commercial Real Estate", exposure: 60, rw: 100 },
  { label: "Past Due 90", exposure: 50, rw: 150 },
  { label: "Other Assets", exposure: 90, rw: 100 },
  // Undrawn commitments at a 50% credit conversion factor.
  {
    label: "Committed Corporate (EAD After 50% CCF)",
    exposure: 240 * 0.5,
    rw: 100,
  },
  { label: "Committed Retail (EAD After 50% CCF)", exposure: 80 * 0.5, rw: 75 },
  { label: "BoG Bills (Zero Risk Weight)", exposure: 260, rw: 0 },
  { label: "GoG Bonds (Zero Risk Weight)", exposure: 360, rw: 0 },
  {
    label: "Cash And Reserves (Zero Risk Weight)",
    exposure: 45 + 175 + 70,
    rw: 0,
  },
];
const CREDIT_RWA_M = sum(CREDIT.map((c) => (c.exposure * c.rw) / 100));
/** Net open FX position 45M × 8% charge × 12.5. */
const MARKET_RWA_M = 45 * 0.08 * 12.5;
/** Basic Indicator Approach: 15% × average gross income (340, 380, 400) × 12.5. */
const OPERATIONAL_RWA_M = ((340 + 380 + 400) * 15 * 12.5) / (3 * 100);
const TOTAL_RWA_M = CREDIT_RWA_M + MARKET_RWA_M + OPERATIONAL_RWA_M;

/** The engine's leverage ratio: Tier 1 over its exposure measure. */
const LEVERAGE_PCT = 10.9375;

test.describe("Basel capital functional workflow", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("reads the fixture's ratios, RWA and capital, stresses, plans and persists", async ({
    page,
  }) => {
    test.setTimeout(150_000);
    // Derived anchors: 340M of capital over 2,147.5M of RWA.
    expect([CET1_M, TIER1_M, TOTAL_CAPITAL_M]).toEqual([260, 280, 340]);
    expect([
      CREDIT_RWA_M,
      MARKET_RWA_M,
      OPERATIONAL_RWA_M,
      TOTAL_RWA_M,
    ]).toEqual([1402.5, 45, 700, 2147.5]);

    // ---- Overview: the four headline ratios.
    await page.goto("/basel");
    await expect(
      page.getByRole("heading", { name: "Basel Capital" }).first(),
    ).toBeVisible();
    await expectKpi(
      page,
      "Capital Adequacy Ratio",
      ratio(TOTAL_CAPITAL_M),
      "BoG minimum 13%",
    );
    await expectKpi(
      page,
      "Tier 1 ratio",
      ratio(TIER1_M),
      "Regulatory minimum 8%",
    );
    await expectKpi(
      page,
      "CET1 ratio",
      ratio(CET1_M),
      "Regulatory minimum 6.50%",
    );
    await expectKpi(
      page,
      "Leverage ratio",
      LEVERAGE_PCT.toFixed(2),
      "Regulatory minimum 6%",
    );
    await expect(
      page.getByText(
        `The CAR of ${((TOTAL_CAPITAL_M / TOTAL_RWA_M) * 100).toFixed(6)}% is at or above the 13% regulatory minimum.`,
      ),
    ).toBeVisible();

    // ---- RWA: the three risk types, and credit RWA exposure by exposure.
    await openTab(page, "RWA");
    await expectKpi(
      page,
      "Credit risk RWA",
      ghsM(CREDIT_RWA_M),
      share(CREDIT_RWA_M),
    );
    await expectKpi(
      page,
      "Market risk RWA",
      ghsM(MARKET_RWA_M),
      share(MARKET_RWA_M),
    );
    await expectKpi(
      page,
      "Operational risk RWA",
      ghsM(OPERATIONAL_RWA_M),
      share(OPERATIONAL_RWA_M),
    );
    await expectKpi(
      page,
      "Total RWA",
      ghsM(TOTAL_RWA_M),
      "Credit + market + operational",
    );
    const credit = section(page, "Credit risk").getByRole("table");
    for (const c of CREDIT) {
      await expectRow(credit, c.label, [
        c.label,
        ghsM(c.exposure),
        `${c.rw}%`,
        ghsM((c.exposure * c.rw) / 100),
      ]);
    }
    await expectRow(credit, "TOTAL CREDIT RISK RWA", [
      "TOTAL CREDIT RISK RWA",
      "—",
      "—",
      ghsM(CREDIT_RWA_M),
    ]);

    // ---- Capital Structure: each tier from its components.
    await openTab(page, "Capital Structure");
    await expectKpi(page, "CET1", full(CET1_M), `${ratio(CET1_M)}% of RWA`);
    await expectKpi(
      page,
      "Tier 1 (CET1 + AT1)",
      full(TIER1_M),
      `${ratio(TIER1_M)}% of RWA`,
    );
    await expectKpi(
      page,
      "Total capital",
      full(TOTAL_CAPITAL_M),
      `${ratio(TOTAL_CAPITAL_M)}% of RWA · CAR`,
    );
    await expect(
      page.getByText(`Tier 1 = CET1 + AT1 = ${full(TIER1_M)}.`),
    ).toBeVisible();

    // ---- Stress: a governed adverse path through every engine.
    await openTab(page, "Stress");
    const run = await runEnterpriseStress(
      page,
      "Adverse domestic downturn (moderate)",
    );
    expect(Number(run.summary.car_erosion_pp)).toBeLessThan(0);
    await expectKpi(page, "Stressed CAR", "16.01%", "Base 17.42%");
    await expectKpi(
      page,
      "CAR erosion",
      "-1.41 pp",
      "Base → stress, final year",
    );
    await expectKpi(page, "Stays above minima", "Yes", "All minima held");
    await expectKpi(page, "Capital gap", "GHS'000 0", "Worst-year, pre-action");
    await expectPersistedStressRun(page, run, "16.01%");

    // ---- Planning: the what-if planner, then refresh and persist ILAAP evidence.
    await openTab(page, "Planning");
    await expect(
      page.getByRole("heading", { name: "Capital Planning" }),
    ).toBeVisible();
    await expectWhatIf(page, {
      rwaGrowthPct: 20,
      retainedPct: 4,
      at1Pct: 2,
      tier2Pct: 1,
    });

    const refreshed = page.waitForResponse(
      (r) =>
        r.url().endsWith("/capital-plan/ilaap-refresh") &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Refresh ILAAP evidence" }).click();
    const response = await refreshed;
    expect(response.status(), await response.text()).toBe(201);
    const snapshot = await response.json();
    const plan = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/capital-plan`,
    );
    expect(plan.latest_ilaap.id).toBe(snapshot.id);
    // The liquidity evidence is the fixture's derived LCR and NSFR (see the Liquidity journey).
    expect(Number(plan.latest_ilaap.lcr_pct)).toBeCloseTo((735 / 499) * 100, 5);
    expect(Number(plan.latest_ilaap.nsfr_pct)).toBeCloseTo(
      (1961 / 1294.5) * 100,
      5,
    );
    const governance = section(page, "ICAAP and ILAAP governance");
    await expect(governance).toContainText(
      `Latest ILAAP evidence${utcDate(snapshot.as_of_date)}`,
    );

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "basel-planning.png"),
        fullPage: true,
      });
    }
  });
});

/**
 * Move every planner slider and check the pro-forma ratios: retained earnings
 * join CET1, new AT1 joins Tier 1, new Tier 2 joins total capital, each sized
 * as a share of today's total capital, over RWA grown by the chosen rate.
 */
async function expectWhatIf(
  page: Page,
  inputs: {
    rwaGrowthPct: number;
    retainedPct: number;
    at1Pct: number;
    tier2Pct: number;
  },
): Promise<void> {
  await page.getByLabel("RWA growth").fill(String(inputs.rwaGrowthPct));
  await page
    .getByLabel("Retained earnings added to CET1")
    .fill(String(inputs.retainedPct));
  await page.getByLabel("New AT1 issuance").fill(String(inputs.at1Pct));
  await page.getByLabel("New Tier 2 issuance").fill(String(inputs.tier2Pct));
  const rwa = TOTAL_RWA_M * (1 + inputs.rwaGrowthPct / 100);
  const cet1 = CET1_M + (inputs.retainedPct / 100) * TOTAL_CAPITAL_M;
  const tier1 = cet1 + AT1_M + (inputs.at1Pct / 100) * TOTAL_CAPITAL_M;
  const total = tier1 + TIER2_M + (inputs.tier2Pct / 100) * TOTAL_CAPITAL_M;
  await expect(page.getByText(`Pro-forma RWA ${ghsM(rwa)}`)).toBeVisible();
  await expectKpi(page, "Pro-forma CAR", ((total / rwa) * 100).toFixed(2));
  await expectKpi(
    page,
    "Pro-forma Tier 1",
    ((tier1 / rwa) * 100).toFixed(2),
    `Now ${ratio(TIER1_M)}%`,
  );
  await expectKpi(
    page,
    "Pro-forma CET1",
    ((cet1 / rwa) * 100).toFixed(2),
    `Now ${ratio(CET1_M)}%`,
  );
}

/** A capital amount over total RWA, as the dashboard prints the ratio. */
function ratio(capitalM: number): string {
  return ((capitalM / TOTAL_RWA_M) * 100).toFixed(2);
}

function share(rwaM: number): string {
  return `${((rwaM / TOTAL_RWA_M) * 100).toFixed(1)}% of total`;
}

/** `fmtCurrencyFull`: the whole amount with thousands separators. */
function full(millions: number): string {
  return `GHS ${(millions * 1e6).toLocaleString("en-US")}`;
}

/** `fmtDateUTC`: "30 Sep 2026". */
function utcDate(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

function sum(values: number[]): number {
  return values.reduce((t, v) => t + v, 0);
}
