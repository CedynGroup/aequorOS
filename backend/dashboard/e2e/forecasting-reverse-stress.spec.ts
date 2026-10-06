// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Reverse stress, worked end to end by an analyst: run the frontier search for
 * the selected reporting period and read the breach multipliers, the ratios at
 * breach and the narrative, then confirm the saved frontier is anchored to that
 * period's own liquidity and capital baseline runs.
 *
 * The source contract is the implemented explicit-period one (#213): the
 * frontier searches the SELECTED period's sealed facts, never a silently
 * substituted current book, and records both engines' baseline input hashes for
 * that period. A period without the inputs is refused, not estimated; the
 * fresh-institution journey shows that refusal.
 *
 * The expectations are the FIXTURE's, not the screen's. The liquidity axis
 * scales the canonical `combined` scenario: run-offs move linearly from the
 * governed base rates toward the shocked rates, inflows from 100% toward 67%
 * (capped at 75% of outflows), and the 8% haircut applies to the marketable
 * Level 1 securities only. The capital axis scales the `severe` scenario's
 * quarterly RWA growth (4%) and credit losses (30.8M) against its 2M quarterly
 * income, and its FX RWA multiplier (1.6) from 1, over the Basel journey's CET1
 * of 260M and RWA build-up. The spec replays the engine's bisection over
 * k ∈ (0, 5] to 0.05 itself.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID, apiGet, expectKpi } from "./support/figures";
import { mintBackendToken } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** 30-day outflows (GHS millions): governed base and `combined` run-off rates. */
const OUTFLOWS = [
  { balance: 700, base: 5, shocked: 15 },
  { balance: 440, base: 10, shocked: 20 },
  { balance: 240, base: 25, shocked: 40 },
  { balance: 200, base: 40, shocked: 60 },
  { balance: 320, base: 100, shocked: 100 },
  { balance: 60, base: 0, shocked: 0 },
  { balance: 100, base: 0, shocked: 0 },
  { balance: 80, base: 10, shocked: 20 },
  { balance: 240, base: 30, shocked: 50 },
];
/** Contractual inflows at their governed rates. */
const INFLOWS = [
  { balance: 60, rate: 50 },
  { balance: 90, rate: 50 },
  { balance: 45, rate: 100 },
];
/** Level 1 HQLA: marketable securities (haircut) and the cash they source. */
const MARKETABLE_HQLA = 260 + 360;
const CASH_HQLA = 45 + 70;
const LCR_MIN_PCT = 100;

/** The capital position the `severe` scenario stresses, GHS millions. */
const CET1 = 260;
const CREDIT_RWA = 1402.5;
const MARKET_RWA = 45;
const OPERATIONAL_RWA = 700;
const CET1_MIN_PCT = 6.5;

function lcrAt(k: number): number {
  const haircut = Math.min(100, 8 * k);
  const hqla = CASH_HQLA + MARKETABLE_HQLA * (1 - haircut / 100);
  const outflows = sum(
    OUTFLOWS.map(
      (o) =>
        (o.balance *
          Math.min(100, Math.max(0, o.base + (o.shocked - o.base) * k))) /
        100,
    ),
  );
  const multiplier = Math.max(0, 1 + (0.67 - 1) * k);
  const inflows = sum(
    INFLOWS.map((i) => (i.balance * i.rate * multiplier) / 100),
  );
  return ratioPct(
    (hqla / (outflows - Math.min(inflows, 0.75 * outflows))) * 100,
  );
}

/** The worst of the four stressed quarters (and Q0) under `severe` × k. */
function worstCet1At(k: number): number {
  const ratios = [0, 1, 2, 3, 4].map((q) => {
    const cet1 = CET1 + q * (2 - 30.8 * k);
    const credit = CREDIT_RWA * (1 + (4 * k) / 100) ** q;
    const market = q >= 1 ? MARKET_RWA * (1 + 0.6 * k) : MARKET_RWA;
    return ratioPct((cet1 / (credit + market + OPERATIONAL_RWA)) * 100);
  });
  return Math.min(...ratios);
}

/**
 * The engine's search: bisect (0, 5] until the bracket is within 0.05, keep the
 * breaching end, and report it to two decimals (Decimal's ROUND_HALF_EVEN).
 */
function frontier(breaches: (k: number) => boolean): number {
  let [low, high] = [0, 5];
  while (high - low > 0.05) {
    const mid = (low + high) / 2;
    if (breaches(mid)) high = mid;
    else low = mid;
  }
  const scaled = high * 100;
  const floor = Math.floor(scaled);
  const rest = scaled - floor;
  return (
    (rest > 0.5 || (rest === 0.5 && floor % 2 === 1) ? floor + 1 : floor) / 100
  );
}

test.describe("Forecasting reverse stress", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("finds the fixture's frontier for the selected period, anchored to its baselines", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const liquidityK = frontier((k) => lcrAt(k) < LCR_MIN_PCT);
    const capitalK = frontier((k) => worstCet1At(k) < CET1_MIN_PCT);
    // Hand-checkable anchors: the unstressed LCR and CET1 the axes start from.
    expect(lcrAt(0)).toBeCloseTo((735 / 499) * 100, 5);
    expect(worstCet1At(0)).toBeCloseTo((260 / 2147.5) * 100, 5);
    expect([liquidityK, capitalK]).toEqual([0.74, 0.94]);
    const lcrAtBreach = lcrAt(liquidityK);
    const cet1AtBreach = worstCet1At(capitalK);

    // The selected period's official baselines, which the frontier must cite.
    const periods = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    );
    const period = periods.periods[0];
    const [liquidityBaseline, capitalBaseline] = await Promise.all(
      ["liquidity", "capital"].map((module) =>
        apiPost(page, "/regulatory-runs", {
          module,
          reporting_period_id: period.id,
          scenario_code: "baseline",
        }),
      ),
    );

    // ---- Run the search from the page.
    await page.goto("/forecasting/reverse-stress");
    const searched = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/banks/${SAMPLE_BANK_ID}/reverse-stress/runs`) &&
        r.request().method() === "POST",
    );
    await page
      .getByRole("button", { name: "Run reverse stress" })
      .first()
      .click();
    const response = await searched;
    expect(response.status()).toBe(201);
    const frontierRun = await response.json();
    expect(frontierRun.reporting_period_id).toBe(period.id);

    // ---- The headline figures and the frontier chart.
    await expectKpi(
      page,
      "Liquidity frontier (combined scenario)",
      `${liquidityK.toFixed(2)}× severity`,
      `LCR ${lcrAtBreach.toFixed(1)}% at breach vs floor ${LCR_MIN_PCT.toFixed(1)}%`,
    );
    await expectKpi(
      page,
      "Capital frontier (severe scenario)",
      `${capitalK.toFixed(2)}× severity`,
      `Worst CET1 ${cet1AtBreach.toFixed(1)}% vs minimum ${CET1_MIN_PCT.toFixed(1)}%`,
    );
    await expect(page.getByTestId("reverse-stress-breach-value")).toHaveText([
      `${liquidityK.toFixed(2)}×`,
      `${capitalK.toFixed(2)}×`,
    ]);
    await expect(page.getByText(frontierRun.narrative)).toBeVisible();
    expect(frontierRun.narrative).toContain(
      `scaling the 'combined' scenario to ${liquidityK.toFixed(2)}x its configured severity drives the LCR to ${lcrAtBreach.toFixed(6)}%`,
    );

    // ---- What the platform kept: the derived frontier and its provenance.
    const { liquidity_axis: liquidity, capital_axis: capital } = frontierRun;
    expect(liquidity).toMatchObject({
      breached: true,
      scenario_code: "combined",
      breach_multiplier: liquidityK.toFixed(2),
    });
    expect(Number(liquidity.baseline_lcr_pct)).toBeCloseTo(lcrAt(0), 6);
    expect(Number(liquidity.lcr_at_breach_pct)).toBeCloseTo(lcrAtBreach, 5);
    expect(capital).toMatchObject({
      breached: true,
      scenario_code: "severe",
      breach_multiplier: capitalK.toFixed(2),
    });
    expect(Number(capital.baseline_worst_cet1_pct)).toBeCloseTo(
      worstCet1At(0),
      6,
    );
    expect(Number(capital.worst_cet1_at_breach_pct)).toBeCloseTo(
      cet1AtBreach,
      5,
    );

    const stored = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/regulatory-runs/${frontierRun.run_id}`,
    );
    expect(stored.input_hash).toBe(frontierRun.input_hash);
    expect(stored.inputs.reporting_period_id).toBe(period.id);
    expect(stored.inputs.as_of_date).toBe(period.period_end);
    expect(stored.inputs.axes.liquidity.baseline_input_hash).toBe(
      liquidityBaseline.input_hash,
    );
    expect(stored.inputs.axes.capital.baseline_input_hash).toBe(
      capitalBaseline.input_hash,
    );
    const latest = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/reverse-stress/latest?reporting_period_id=${period.id}`,
    );
    expect(latest.run_id).toBe(frontierRun.run_id);

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-reverse-stress.png"),
        fullPage: true,
      });
    }

    // The plotted search range must describe the persisted search, including
    // breached axes whose stored metrics omit an axis-level k_max.
    expect(stored.inputs.search.k_max).toBe("5");
    await expect(
      page.getByTestId("reverse-stress-frontier").getByText("5.00×", {
        exact: true,
      }),
    ).toHaveCount(2);
  });
});

async function apiPost(page: Page, pathName: string, body: unknown) {
  const response = await page.request.post(
    `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}${pathName}`,
    {
      headers: { Authorization: `Bearer ${await mintBackendToken("analyst")}` },
      data: body,
    },
  );
  expect(response.status(), await response.text()).toBe(201);
  const run = await response.json();
  expect(run.status).toBe("succeeded");
  return run;
}

function sum(values: number[]): number {
  return values.reduce((t, v) => t + v, 0);
}

/** The engine's 6 dp ROUND_HALF_UP ratio quantum. */
function ratioPct(value: number): number {
  return Math.round(value * 1e6) / 1e6;
}
