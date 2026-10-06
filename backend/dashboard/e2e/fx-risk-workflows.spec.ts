// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * FX, worked end to end by an analyst: read the fixture's net open positions,
 * limits, VaR, hedge book, forwards and depreciation scenarios back off every
 * FX tab, then drive an enterprise stress from the FX Scenarios tab and confirm
 * its FX leg starts from the same position the module reports and that the run
 * is the one the platform kept.
 *
 * The expectations are the FIXTURE's, not the screen's. Positions are the
 * canonical book's net currency amounts at its period-end spot fixes
 * (`tests/fixtures/canonical_bank_fixture.py`); the spec converts them, sums the
 * long and short legs, takes the larger as the aggregate NOP, sizes every
 * figure against the fixture's Tier 1, applies each depreciation shock to the
 * NOP, and classifies each hedge with the IFRS 9 dual test itself. Only the
 * historical VaR (a 250-observation return series) and the stressed path are
 * pinned as engine output. The book is carried forward unchanged to the latest
 * month end, so none of these figures move with the calendar.
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
  signedGhsM,
} from "./support/figures";
import {
  expectPersistedStressRun,
  runEnterpriseStress,
  type StressRun,
} from "./support/stress";
import { openTab } from "./support/navigation";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

type FxStressRun = StressRun & {
  outcome: {
    fx: {
      base_nop_pct_tier1: string;
      shock_pct: string;
      stressed_nop_pct_tier1: string;
      stressed_within_aggregate_limit: boolean;
    };
  };
};

/** CET1 (150 + 95 + 45 + 10 − 25 intangibles − 15 DTA) + AT1 20, GHS millions. */
const TIER1_M = 280;
const SINGLE_LIMIT_PCT = 10;
const AGGREGATE_LIMIT_PCT = 20;

/** Net currency position (in the currency) and its period-end spot, in GHS. */
const POSITIONS = [
  { ccy: "EUR", net: -500_000, spot: 14.0 },
  { ccy: "GBP", net: 600_000, spot: 15.0 },
  { ccy: "NGN", net: 375_000_000, spot: 0.008 },
  { ccy: "USD", net: 2_400_000, spot: 12.5 },
  { ccy: "XOF", net: -250_000_000, spot: 0.02 },
  { ccy: "ZAR", net: 5_000_000, spot: 0.6 },
].map((p) => ({ ...p, netGhsM: (p.net * p.spot) / 1e6 }));

const LONG_M = sum(
  POSITIONS.filter((p) => p.netGhsM > 0).map((p) => p.netGhsM),
);
const SHORT_M = -sum(
  POSITIONS.filter((p) => p.netGhsM < 0).map((p) => p.netGhsM),
);
/** Aggregate NOP: the larger of the long and short legs. */
const NOP_M = Math.max(LONG_M, SHORT_M);
const LARGEST = POSITIONS.reduce((a, b) =>
  Math.abs(b.netGhsM) > Math.abs(a.netGhsM) ? b : a,
);

/** The governed depreciation shocks, applied to the aggregate NOP. */
const DEPRECIATION = [
  { label: "No shock", shockPct: 0 },
  { label: "Depreciation 10.0%", shockPct: 10 },
  { label: "Depreciation 20.0%", shockPct: 20 },
  { label: "Depreciation 30.0%", shockPct: 30 },
];

/** The hedge book: marks (GHS millions) and the two prospective test results. */
const HEDGES = [
  {
    id: "FXH-USD-01",
    pair: "USD/GHS",
    instrument: "forward",
    mtm: 4.5,
    r2: 94,
    offset: 102,
  },
  {
    id: "FXH-EUR-02",
    pair: "EUR/GHS",
    instrument: "cross_currency_swap",
    mtm: -2.1,
    r2: 88,
    offset: 91,
  },
  {
    id: "FXH-GBP-03",
    pair: "GBP/GHS",
    instrument: "option",
    mtm: 1.3,
    r2: 72,
    offset: 95,
  },
];
/** IFRS 9 dual test: R² ≥ 80% and dollar offset within 80–125%. */
const effective = (h: (typeof HEDGES)[number]) =>
  h.r2 >= 80 && h.offset >= 80 && h.offset <= 125;

/** The engine's 99% one-day historical VaR of the fixture book, GHS. */
const VAR = {
  portfolio: 731_231,
  standaloneTotal: 1_305_385,
  stressed: 1_427_292,
  standalone: {
    EUR: 157_178,
    GBP: 218_745,
    NGN: 73_485,
    USD: 675_720,
  } as Record<string, number>,
};

test.describe("FX functional workflow", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("reads the fixture's NOP, limits, VaR, hedges and scenarios, then stresses and persists", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    // Derived anchors: 45M long against 12M short, so the NOP is the long leg.
    expect([LONG_M, SHORT_M, NOP_M, LARGEST.ccy]).toEqual([45, 12, 45, "USD"]);

    // ---- Exposure: the NOP against Tier 1 and every currency's position.
    await page.goto("/fx");
    await expect(
      page.getByRole("heading", { name: "FX Exposure" }),
    ).toBeVisible();
    await expectKpi(
      page,
      "Aggregate NOP / Tier 1",
      pct(NOP_M / TIER1_M),
      `Limit ${AGGREGATE_LIMIT_PCT}%`,
    );
    await expectKpi(
      page,
      `Largest single currency (${LARGEST.ccy})`,
      pct(Math.abs(LARGEST.netGhsM) / TIER1_M),
      `Single-currency limit ${SINGLE_LIMIT_PCT}%`,
    );
    await expectKpi(
      page,
      "Net open position",
      ghsM(NOP_M),
      `Long ${ghsM(LONG_M)} · Short ${ghsM(SHORT_M)}`,
    );
    await expectKpi(page, "Tier 1 capital", ghsM(TIER1_M), "Limit denominator");
    const positions = section(page, "Position detail").getByRole("table");
    for (const p of POSITIONS) {
      const share = Math.abs(p.netGhsM) / TIER1_M;
      await expectRow(positions, p.ccy, [
        p.ccy,
        p.netGhsM > 0 ? "Long" : "Short",
        p.net.toLocaleString("en-GH"),
        p.spot.toFixed(4),
        signedGhsM(p.netGhsM),
        pct(share),
        share * 100 > SINGLE_LIMIT_PCT ? "Breach" : "Within",
      ]);
    }
    await expectScenarioStrip(page);

    // ---- VaR & Stress: the historical VaR bridge, currency by currency.
    await openTab(page, "VaR & Stress");
    await expectKpi(
      page,
      "Portfolio VaR (99%, 1-day)",
      ghsM(VAR.portfolio / 1e6),
      "250 return observations",
    );
    await expectKpi(
      page,
      "Sum of standalone VaR",
      ghsM(VAR.standaloneTotal / 1e6),
    );
    await expectKpi(
      page,
      "Diversification benefit",
      ghsM((VAR.standaloneTotal - VAR.portfolio) / 1e6),
    );
    await expectKpi(page, "Stressed VaR", ghsM(VAR.stressed / 1e6));
    const standalone = section(page, "Standalone VaR by currency").getByRole(
      "table",
    );
    for (const [ccy, v] of Object.entries(VAR.standalone)) {
      const p = POSITIONS.find((x) => x.ccy === ccy)!;
      await expectRow(standalone, ccy, [
        ccy,
        signedGhsM(p.netGhsM),
        ghsM(v / 1e6),
      ]);
    }
    expect(sum(Object.values(VAR.standalone)) <= VAR.standaloneTotal).toBe(
      true,
    );

    // ---- Hedges: the IFRS 9 dual test applied to every hedge.
    await openTab(page, "Hedge Book");
    const passing = HEDGES.filter(effective).length;
    await expectKpi(
      page,
      "Aggregate hedge MTM",
      signedGhsM(sum(HEDGES.map((h) => h.mtm))),
    );
    await expectKpi(page, "Effective hedges", `${passing} of ${HEDGES.length}`);
    const hedges = section(page, "Hedge inventory").getByRole("table");
    for (const h of HEDGES) {
      await expectRow(hedges, h.id, [
        h.id,
        h.pair,
        h.instrument,
        `${h.r2.toFixed(1)}%`,
        `${h.offset.toFixed(1)}%`,
        signedGhsM(h.mtm),
      ]);
      await expect(
        hedges.locator("tbody tr").filter({ hasText: h.id }),
      ).toContainText(effective(h) ? "Effective" : "Ineffective");
    }

    // ---- Limits: the aggregate ceiling and the single-currency breach.
    await openTab(page, "Limits");
    await expectKpi(
      page,
      "Aggregate limit utilisation",
      `${((NOP_M / TIER1_M / (AGGREGATE_LIMIT_PCT / 100)) * 100).toFixed(1)}%`,
      `${pct(NOP_M / TIER1_M)} of Tier 1 vs ${AGGREGATE_LIMIT_PCT}% ceiling`,
    );
    await expect(
      page.getByText(
        `The largest single-currency net open position (USD at ${((Math.abs(LARGEST.netGhsM) / TIER1_M) * 100).toFixed(6)}% of Tier 1) is above the 10% single-currency limit.`,
      ),
    ).toBeVisible();

    // ---- Forwards: the spot fixes the NOP was revalued at. No forward curve
    // can be ingested yet (#333), so the monitor states that honestly.
    await openTab(page, "Forwards");
    await expect(
      page.getByText("No forward curve ingested", { exact: true }),
    ).toBeVisible();
    const spots = section(page, "Spot reference").getByRole("table");
    for (const p of POSITIONS) {
      await expectRow(spots, `${p.ccy}/GHS`, [
        `${p.ccy}/GHS`,
        p.spot.toFixed(4),
        p.netGhsM > 0 ? "Long" : "Short",
      ]);
    }

    // ---- Scenarios: a governed macro path through every engine, FX leg included.
    await openTab(page, "Scenarios");
    await expect(
      page.getByRole("heading", { name: "Enterprise Stress Workbench" }),
    ).toBeVisible();
    const run = (await runEnterpriseStress(
      page,
      "Severe stagflation (severe)",
    )) as FxStressRun;
    expect(run.scenario_code).toBe("system_severe_stagflation");
    expectSevereFxOutcome(run.outcome.fx);
    await expectKpi(page, "Stressed CAR", "15.21%", "Base 17.42%");
    await expectKpi(page, "Stressed LCR", "116.6%", "Base 147.3%");
    await expectKpi(page, "Solvency × liquidity", "Both hold");
    await expectPersistedStressRun(page, run, "15.21%");
    const latest = await apiGet<FxStressRun>(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/enterprise-stress/latest?reporting_period_id=${run.reporting_period_id}&scenario_id=${run.scenario_id}`,
    );
    expect(latest.run_id).toBe(run.run_id);
    expectSevereFxOutcome(latest.outcome.fx);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "fx-stress.png"),
        fullPage: true,
      });
    }
  });
});

function expectSevereFxOutcome(fx: FxStressRun["outcome"]["fx"]): void {
  const baseNopPct = (NOP_M / TIER1_M) * 100;
  const shockPct = Number(fx.shock_pct);
  const stressedNopPct = baseNopPct * (1 + shockPct / 100);
  expect(Number(fx.base_nop_pct_tier1)).toBeCloseTo(baseNopPct, 6);
  expect(shockPct).toBeCloseTo(29.807692, 6);
  expect(Number(fx.stressed_nop_pct_tier1)).toBeCloseTo(stressedNopPct, 6);
  expect(stressedNopPct).toBeGreaterThan(AGGREGATE_LIMIT_PCT);
  expect(fx.stressed_within_aggregate_limit).toBe(false);
}

/** The depreciation strip: each shock grows the NOP and is held to the ceiling. */
async function expectScenarioStrip(page: Page): Promise<void> {
  const strip = section(page, "Depreciation scenarios");
  for (const s of DEPRECIATION) {
    const nop = NOP_M * (1 + s.shockPct / 100);
    const card = strip.locator("div").filter({ hasText: s.label }).last();
    await expect(card).toContainText(pct(nop / TIER1_M));
    await expect(card).toContainText(
      `NOP ${ghsM(nop)} · limit ${AGGREGATE_LIMIT_PCT}%`,
    );
  }
}

function pct(ratio: number): string {
  return `${(ratio * 100).toFixed(2)}%`;
}

function sum(values: number[]): number {
  return values.reduce((t, v) => t + v, 0);
}
