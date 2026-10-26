import path from "node:path";
import { E2E_TMP } from "../playwright.config";
import { expectKpi } from "./support/figures";
import { biApi } from "./support/bi";
import { test, expect, type Liquidity } from "./support/treasury-boundary";

const base = { cash: "31000", deposits: "100000", equity: "131000" };
test.use({
  storageState: path.join(E2E_TMP, "analyst.json"),
  storyConfig: { ownedTenant: true, roles: ["admin", "analyst", "approver"] },
});
test.beforeEach(() => test.setTimeout(180_000));

function figures(
  result: Liquidity,
  hqla: number,
  net: number,
  lcr: number,
  status: string,
) {
  expect(result.metrics.hqla_total_ghs).not.toBeNull();
  expect(result.metrics.net_outflows_30d_ghs).not.toBeNull();
  expect(result.metrics.lcr_pct).not.toBeNull();
  expect(Number(result.metrics.hqla_total_ghs)).toBe(hqla);
  expect(Number(result.metrics.net_outflows_30d_ghs)).toBe(net);
  expect(Number(result.metrics.lcr_pct)).toBe(lcr);
  expect(result.metrics.lcr_status).toBe(status);
}

test("zero uploaded cash produces a real zero HQLA and red 0% coverage", async ({
  treasury,
  page,
}) => {
  const result = await treasury.upload({
    ...base,
    cash: "0",
    equity: "100000",
  });
  figures(result, 0, 31_000, 0, "red");
  await page.goto("/liquidity");
  await expectKpi(page, "HQLA stock", "GHS 0", "Post-haircut weighted");
  await expectKpi(page, "Liquidity Coverage Ratio", "0.00%");
});

test("correcting cash across the 100% LCR threshold changes amber to green at the boundary", async ({
  treasury,
  page,
}) => {
  // Corporate funding: 60,000 at 25% + 40,000 at 40% = 31,000 outflow.
  for (const point of [
    {
      cash: "30996.90",
      equity: "130996.90",
      lcr: 99.99,
      status: "amber",
      passed: false,
      shown: "99.99%",
    },
    {
      cash: "31000",
      equity: "131000",
      lcr: 100,
      status: "green",
      passed: true,
      shown: "100.00%",
    },
    {
      cash: "31003.10",
      equity: "131003.10",
      lcr: 100.01,
      status: "green",
      passed: true,
      shown: "100.01%",
    },
  ]) {
    const result = await treasury.upload({
      ...base,
      cash: point.cash,
      equity: point.equity,
    });
    figures(result, Number(point.cash), 31_000, point.lcr, point.status);
    expect(
      result.validations.find((row) => row.rule_code === "lcr_above_minimum")
        ?.passed,
    ).toBe(point.passed);
    await page.goto("/liquidity");
    await expectKpi(page, "Liquidity Coverage Ratio", point.shown);
  }
});

test("a loan repaying within 30 days binds the 75% inflow cap and leaves 7,750 net outflow", async ({
  treasury,
  page,
}) => {
  const result = await treasury.upload({ ...base, loanMaturityDays: 10 });
  figures(result, 31_000, 7_750, 400, "green");
  expect(
    result.inflows.reduce(
      (total, row) => total + Number(row.weighted_amount),
      0,
    ),
  ).toBeGreaterThan(23_250);
  expect(
    result.validations.find((row) => row.rule_code === "inflow_cap_applied")
      ?.message,
  ).toMatch(/75% inflow cap bound:.*capped at 23250/);
  await page.goto("/liquidity");
  await expectKpi(
    page,
    "30-day net outflows",
    "GHS 7.8K",
    "Outflows − capped inflows",
  );
  await expectKpi(page, "Liquidity Coverage Ratio", "400.00%");
});

test("Level 2A and 2B holdings receive haircuts and both caps before the 50,000 HQLA figure", async ({
  treasury,
  page,
}) => {
  const result = await treasury.upload({
    ...base,
    cash: "30000",
    equity: "330000",
    securitiesBalance: "200000",
    securities: [
      { reference: "TREASURY-L2A", amount: "100000", level: "L2A" },
      { reference: "TREASURY-L2B", amount: "100000", level: "L2B" },
    ],
  });
  figures(result, 50_000, 31_000, 161.290323, "green");
  const lines = result.hqla_composition;
  for (const [code, haircut, weighted] of [
    ["hqla_level2a", 15, 85_000],
    ["hqla_level2b", 50, 50_000],
  ] as const) {
    const line = lines.find((row) => row.line_code === code);
    expect(line).toBeDefined();
    expect(Number(line!.exposure_amount)).toBe(100_000);
    expect(Number(line!.rate_pct)).toBe(haircut);
    expect(Number(line!.weighted_amount)).toBe(weighted);
  }
  expect(
    Number(
      lines.find((row) => row.line_code === "hqla_level2b_cap_adjustment")
        ?.weighted_amount,
    ),
  ).toBe(-42_500);
  expect(
    Number(
      lines.find((row) => row.line_code === "hqla_level2_cap_adjustment")
        ?.weighted_amount,
    ),
  ).toBe(-72_500);
  await page.goto("/liquidity");
  await expectKpi(page, "HQLA stock", "GHS 50.0K", "Post-haircut weighted");
  await expectKpi(page, "Liquidity Coverage Ratio", "161.29%");
});

test("an uploaded public-sector holding without Basel classification stays in the book but contributes no HQLA", async ({
  treasury,
  page,
}) => {
  const result = await treasury.upload({
    ...base,
    equity: "231000",
    securitiesBalance: "100000",
    securities: [{ reference: "TREASURY-UNCLASSIFIED", amount: "100000" }],
  });
  figures(result, 31_000, 31_000, 100, "green");
  const positions = await treasury.read<{
    positions: { source_reference: string; balance: string }[];
  }>("/canonical-positions");
  const holding = positions.positions.find(
    (row) => row.source_reference === "TREASURY-UNCLASSIFIED",
  );
  expect(holding).toBeDefined();
  expect(Number(holding!.balance)).toBe(100_000);
  expect(
    result.hqla_composition.reduce(
      (total, row) => total + Number(row.weighted_amount),
      0,
    ),
  ).toBe(31_000);
  await page.goto("/liquidity");
  await expectKpi(page, "HQLA stock", "GHS 31.0K", "Post-haircut weighted");
  await expectKpi(page, "Liquidity Coverage Ratio", "100.00%");
});

test.describe("savings and loans capital regime", () => {
  test.use({
    treasuryReturnCode: "NPL-MONTHLY",
    treasuryModule: "capital",
    storyConfig: {
      ownedTenant: true,
      institutionType: "savings_and_loans",
      roles: ["admin", "analyst", "approver"],
    },
  });
  test("SDI capital uses its s.29 floor while bank projections are not applicable", async ({
    treasury,
    page,
    request,
  }) => {
    await treasury.refresh(base);
    const capital = await treasury.read<{
      net_own_funds_ghs: string;
      total_rwa_ghs: string;
      car_pct: string | null;
      car_min_pct: string;
      computable: boolean;
      status: string;
    }>("/sdi/capital-summary");
    expect(capital).toMatchObject({ computable: true, status: "green" });
    expect(Number(capital.net_own_funds_ghs)).toBe(131_000);
    expect(Number(capital.total_rwa_ghs)).toBe(200_000);
    expect(Number(capital.car_min_pct)).toBe(10);
    expect(Number(capital.car_pct)).toBe(65.5);
    const forecast = await biApi(
      request,
      "analyst",
      `/banks/${treasury.bankId}/forecast/scenarios`,
    );
    expect(forecast.status, JSON.stringify(forecast.body)).toBe(409);
    expect(JSON.stringify(forecast.body)).toContain(
      "No projection method has been approved for this licence class",
    );
    await page.goto("/basel");
    await expectKpi(page, "Capital Adequacy Ratio", "65.50%");
    await expect(page.getByText("s.29 minimum", { exact: true })).toBeVisible();
    await expect(page.getByText("CET1 ratio", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Tier 1 ratio", { exact: true })).toHaveCount(
      0,
    );
  });
});
