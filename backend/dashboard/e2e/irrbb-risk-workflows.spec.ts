// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * IRRBB, worked end to end by an analyst: run the scenario set, then read the
 * fixture's repricing ladder, earnings-at-risk, EVE sensitivity and limits back
 * off every tab, and confirm the run the screen started is the one the
 * platform kept.
 *
 * The expectations are the FIXTURE's, not the screen's. The ladder below is the
 * canonical book's repricing positions (`tests/fixtures/canonical_bank_fixture.py`
 * `_IRR_ASSET_POSITIONS`, `_IRR_LIABILITY_POSITIONS`, `_IRR_SWAP`), summed per
 * bucket here; every gap, cumulative gap and EaR figure is derived from it with
 * the engine's documented formula. Tier 1 is the fixture's capital components.
 * Only the EVE re-pricing (a discount-curve calculation) is pinned as engine
 * output, and its ratio to Tier 1 is still checked against the derived Tier 1.
 * The book is carried forward unchanged to the latest month end, so none of
 * these figures move with the calendar.
 *
 * The Scenarios tab is the enterprise stress workbench with the IRRBB lens: a
 * governed rate path driven through every engine. Its capital and liquidity
 * legs are asserted; its own IRRBB leg is not, because it currently prices a
 * different book from this module (#306).
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
  signedGhsM,
} from "./support/figures";
import {
  expectPersistedStressRun,
  runEnterpriseStress,
} from "./support/stress";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** Repricing positions per bucket, GHS millions, as the fixture books them. */
const LADDER = [
  { bucket: "overnight", midpointYears: 0.003, rsa: [70], rsl: [240] },
  { bucket: "1-7d", midpointYears: 0.014, rsa: [60], rsl: [] },
  { bucket: "8-30d", midpointYears: 0.06, rsa: [90], rsl: [200] },
  // The pay-fixed swap's floating receive leg reprices here (notional 120).
  {
    bucket: "1-3m",
    midpointYears: 0.17,
    rsa: [110, 180, 120],
    rsl: [280, 320],
  },
  { bucket: "3-6m", midpointYears: 0.38, rsa: [120, 150], rsl: [300] },
  { bucket: "6-12m", midpointYears: 0.75, rsa: [90, 200], rsl: [220] },
  // ...and its fixed pay leg sits at the 3-year tenor.
  { bucket: "1-3y", midpointYears: 1.9, rsa: [150, 240], rsl: [100, 120] },
  { bucket: "3-5y", midpointYears: 4.0, rsa: [180, 200], rsl: [] },
  { bucket: "5y+", midpointYears: 7.0, rsa: [100, 100], rsl: [45] },
].map((row) => ({
  ...row,
  totalRsa: sum(row.rsa),
  totalRsl: sum(row.rsl),
  gap: sum(row.rsa) - sum(row.rsl),
  midpointMonths: row.midpointYears * 12,
}));

const CUMULATIVE = LADDER.map((_, i) =>
  sum(LADDER.slice(0, i + 1).map((r) => r.gap)),
);

/** CET1 (150 + 95 + 45 + 10 − 25 intangibles − 15 DTA) + AT1 20. */
const TIER1_M = 280;
const EVE_LIMIT_PCT = 15;

/**
 * ΔNII over `horizonMonths` for a parallel shock of `bp`, GHS millions:
 * Σ Gap_i · Δr · (H − m_i)/H over the buckets whose midpoint is inside H.
 */
function earningsAtRisk(horizonMonths: number, bp: number): number {
  return sum(
    LADDER.filter((r) => r.midpointMonths < horizonMonths).map(
      (r) =>
        ((r.gap * bp) / 10_000) *
        ((horizonMonths - r.midpointMonths) / horizonMonths),
    ),
  );
}

function gapInside(horizonMonths: number): number {
  return sum(
    LADDER.filter((r) => r.midpointMonths < horizonMonths).map((r) => r.gap),
  );
}

/**
 * The engine's EVE re-pricing of the fixture book, GHS millions: base EVE, and
 * each scenario's EVE with its ΔEVE against that base.
 */
const BASE_EVE_M = -100.562866;
const EVE_SCENARIOS = [
  {
    label: "Parallel +200bp",
    shape: "Parallel upward shift of the full curve",
    eve: -114.398373,
    delta: -13.835507,
  },
  {
    label: "Parallel −200bp",
    shape: "Parallel downward shift of the full curve",
    eve: -85.57589,
    delta: 14.986975,
  },
  {
    label: "Short +250bp",
    shape: "Short-end rates shocked up, long end anchored",
    eve: -105.533046,
    delta: -4.970181,
  },
  {
    label: "Short −250bp",
    shape: "Short-end rates shocked down, long end anchored",
    eve: -95.454007,
    delta: 5.108858,
  },
  {
    label: "Steepener",
    shape: "Short rates down, long rates up — curve steepens",
    eve: -102.794363,
    delta: -2.231498,
  },
  {
    label: "Flattener",
    shape: "Short rates up, long rates down — curve flattens",
    eve: -100.049242,
    delta: 0.513623,
  },
  {
    label: "Parallel +450bp",
    shape: "Jurisdiction-calibrated parallel upward shift (informational)",
    eve: -130.241496,
    delta: -29.678631,
  },
  {
    label: "Parallel −450bp",
    shape: "Jurisdiction-calibrated parallel downward shift (informational)",
    eve: -65.029657,
    delta: 35.533209,
  },
];
const WORST = EVE_SCENARIOS[1];

test.describe("IRRBB functional workflow", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("runs the scenario set and reads the fixture's ladder, EaR, EVE and limits", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const ear12 = earningsAtRisk(12, 200);
    // The regulatory 12-month EaR, derived: −7.4506M on a −370M ≤12m gap.
    expect(ear12).toBeCloseTo(-7.4506, 6);
    expect(gapInside(12)).toBe(-370);

    await page.goto("/irr");
    await expect(
      page.getByRole("heading", { name: "Interest Rate Risk" }).first(),
    ).toBeVisible();

    // ---- Overview: the headline KPIs and the engine's own rule messages.
    await expectKpi(
      page,
      "Worst ΔEVE / Tier 1",
      pct(WORST.delta / TIER1_M),
      `${WORST.label} · limit ${EVE_LIMIT_PCT}%`,
    );
    await expectKpi(
      page,
      "Duration gap",
      "0.48",
      "Assets 0.83y · Liabilities 0.33y",
    );
    await expectKpi(
      page,
      "NII sensitivity ±200bp",
      signedGhsM(ear12),
      `+200bp ${signedGhsM(ear12)} · −200bp ${signedGhsM(-ear12)}`,
    );
    await expect(
      page.getByText(
        `8 rate shocks · Base EVE ${ghsM(BASE_EVE_M)} · Tier 1 ${ghsM(TIER1_M)}`,
      ),
    ).toBeVisible();
    await expect(
      page.getByText(
        "The parallel ±200 bp earnings-at-risk of 2.9289% of base net interest income is within the 10% limit.",
      ),
    ).toBeVisible();

    // ---- Run the scenario set. The response is the persisted batch.
    const runButton = page.getByRole("button", { name: "Run IRRBB scenarios" });
    await expect(runButton).toBeEnabled();
    const minted = page.waitForResponse(
      (response) =>
        response.url().endsWith("/irr/run-all-scenarios") &&
        response.request().method() === "POST",
    );
    await runButton.click();
    const response = await minted;
    expect(response.status()).toBe(201);
    const batch = await response.json();
    expect(
      batch.runs.map((run: { scenario_code: string }) => run.scenario_code),
    ).toEqual([
      "baseline",
      "parallel_up_200",
      "parallel_down_200",
      "short_up_250",
      "short_down_250",
      "steepener",
      "flattener",
    ]);
    const baseline = batch.runs[0];
    expect(baseline.status).toBe("succeeded");
    const periodLabel: string = baseline.inputs.reporting_period.label;

    // The platform kept exactly what it computed, and it is the fixture's book.
    const persisted = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/regulatory-runs/${baseline.id}`,
    );
    expect(persisted.input_hash).toBe(baseline.input_hash);
    const metrics = persisted.metrics;
    expect(Number(metrics.tier1_ghs)).toBe(TIER1_M * 1e6);
    expect(Number(metrics.cumulative_12m_gap_ghs)).toBe(gapInside(12) * 1e6);
    expect(Number(metrics.ear_up_200_ghs)).toBeCloseTo(ear12 * 1e6, 2);
    expect(Number(metrics.ear_down_200_ghs)).toBeCloseTo(-ear12 * 1e6, 2);
    expect(metrics.worst_scenario).toBe("parallel_down_200");
    expect(
      metrics.gap_buckets.map(
        (b: {
          bucket: string;
          rsa_ghs: string;
          rsl_ghs: string;
          cumulative_gap_ghs: string;
        }) => [
          b.bucket,
          Number(b.rsa_ghs) / 1e6,
          Number(b.rsl_ghs) / 1e6,
          Number(b.cumulative_gap_ghs) / 1e6,
        ],
      ),
    ).toEqual(
      LADDER.map((r, i) => [r.bucket, r.totalRsa, r.totalRsl, CUMULATIVE[i]]),
    );
    expect(
      metrics.eve_by_scenario.map(
        (s: { delta_eve_ghs: string }) =>
          Math.round(Number(s.delta_eve_ghs)) / 1e6,
      ),
    ).toEqual(EVE_SCENARIOS.map((s) => s.delta));
    await expect(runButton).toHaveText("Run IRRBB scenarios");

    // ---- Gap Analysis: the full ladder, bucket by bucket.
    await page.getByRole("link", { name: "Gap Analysis", exact: true }).click();
    await expect(page).toHaveURL(/\/irr\/gaps$/);
    await expectKpi(
      page,
      "12-month cumulative gap",
      signedGhsM(gapInside(12)),
      "Liability-sensitive over 12 months",
    );
    const ladderTable = page.getByRole("table");
    for (const [i, row] of LADDER.entries()) {
      await expectRow(ladderTable, row.bucket, [
        row.bucket,
        `${row.midpointYears.toFixed(2)}y`,
        ghsM(row.totalRsa),
        ghsM(row.totalRsl),
        signedGhsM(row.gap),
        signedGhsM(CUMULATIVE[i]),
        row.midpointMonths < 12 ? "EaR window" : "—",
      ]);
    }
    // Drill into the bucket the swap's receive leg lands in.
    await page.getByRole("cell", { name: "1-3m", exact: true }).click();
    await expect(page.getByText("1-3m — bucket drill-down")).toBeVisible();
    await expect(page.locator("dl")).toContainText(
      `Rate-sensitive assets${ghsM(410)}`,
    );
    await expect(page.locator("dl")).toContainText(
      `Rate-sensitive liabilities${ghsM(600)}`,
    );

    // ---- EVE & NII: every scenario, then a desk horizon the engine computes.
    await page.getByRole("link", { name: "EVE & NII", exact: true }).click();
    await expect(page).toHaveURL(/\/irr\/sensitivity$/);
    const eveTable = page.getByRole("table");
    for (const scenario of EVE_SCENARIOS) {
      await expectRow(eveTable, scenario.label, [
        scenario.label,
        scenario.shape,
        ghsM(scenario.eve),
        signedGhsM(scenario.delta),
        pct(scenario.delta / TIER1_M),
        Math.abs(scenario.delta / TIER1_M) * 100 > EVE_LIMIT_PCT
          ? "Breach"
          : "Within limit",
      ]);
    }
    await expectKpi(
      page,
      "ΔNII — rates +200bp",
      signedGhsM(ear12),
      "regulatory 12-month horizon",
    );
    await expectKpi(
      page,
      "ΔNII — rates −200bp",
      signedGhsM(-ear12),
      "regulatory 12-month horizon",
    );

    const deskRead = page.waitForResponse(
      (r) =>
        /\/irr\/ear-analysis\?/.test(r.url()) &&
        /horizon_months=6/.test(r.url()),
    );
    await page.getByLabel("Desk EaR horizon in months").selectOption("6");
    expect((await deskRead).status()).toBe(200);
    const desk = "Desk analysis · 6-month horizon";
    await expectKpi(
      page,
      "ΔNII — rates +200bp",
      signedGhsM(earningsAtRisk(6, 200)),
      desk,
    );
    await expectKpi(
      page,
      "ΔNII — rates −200bp",
      signedGhsM(-earningsAtRisk(6, 200)),
      desk,
    );
    await expectKpi(
      page,
      "Cumulative gap inside horizon",
      signedGhsM(gapInside(6)),
    );
    // The desk view is an analysis, not a restatement: the regulatory figures hold.
    await expectKpi(
      page,
      "ΔNII — rates +200bp",
      signedGhsM(ear12),
      "regulatory 12-month horizon",
    );

    // ---- Limits: the worst case against the supervisory ceiling, and the run
    // this journey minted now backs the period's breach-history entry.
    await page.getByRole("link", { name: "Limits", exact: true }).click();
    await expect(page).toHaveURL(/\/irr\/limits$/);
    await expect(
      page.getByRole("img", {
        name: `${pct(WORST.delta / TIER1_M)} of ${EVE_LIMIT_PCT.toFixed(2)}% supervisory limit`,
        exact: true,
      }),
    ).toBeVisible();
    for (const scenario of EVE_SCENARIOS) {
      await expect(
        page.getByRole("img", {
          name: `${pct(Math.abs(scenario.delta) / TIER1_M)} of ${EVE_LIMIT_PCT.toFixed(2)}% limit`,
          exact: true,
        }),
      ).toBeVisible();
    }
    await expectStoredTrendPoint(page, periodLabel, pct(WORST.delta / TIER1_M));

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "irrbb-limits.png"),
        fullPage: true,
      });
    }
  });

  test("drives the IRRBB parallel +200bp stress from the Scenarios tab and persists it", async ({
    page,
  }) => {
    await page.goto("/irr");
    await page.getByRole("link", { name: "Scenarios", exact: true }).click();
    await expect(page).toHaveURL(/\/irr\/scenarios$/);
    await expect(
      page.getByRole("heading", { name: "Enterprise Stress Workbench" }),
    ).toBeVisible();

    const run = await runEnterpriseStress(
      page,
      "IRRBB parallel +200bp (moderate)",
    );
    expect(run.scenario_code).toBe("system_irr_parallel_up_200");
    // The engine's three-year path for the fixture book under a parallel +200bp
    // rate path: capital erodes by 0.14pp and coverage falls from the fixture's
    // derived 147.29% (see the Liquidity journey) to 138.60%.
    expect(Number(run.summary.baseline_lcr_pct)).toBeCloseTo(147.294589, 6);
    await expectKpi(page, "Stressed CAR", "17.28%", "Base 17.42%");
    await expectKpi(
      page,
      "CAR erosion",
      "-0.14 pp",
      "Base → stress, final year",
    );
    await expectKpi(page, "Stressed LCR", "138.6%", "Base 147.3%");
    await expectKpi(page, "Stays above minima", "Yes", "All minima held");
    await expectKpi(page, "Solvency × liquidity", "Both hold");

    await expectPersistedStressRun(page, run, "17.28%");
  });
});

/** The breach-history entry for `label` is backed by a stored run, not inline. */
async function expectStoredTrendPoint(
  page: Page,
  label: string,
  value: string,
): Promise<void> {
  const point = page.locator(`[title^="${label} · period end"]`);
  await expect(point).toHaveCount(1);
  await expect(point).not.toHaveAttribute("title", /computed inline/);
  await expect(point).toHaveText(`${label}${value}`);
}

/** `fmtPct(ratio × 100, 2)`. */
function pct(ratio: number): string {
  return `${(ratio * 100).toFixed(2)}%`;
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0);
}
