// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * FTP, worked end to end by an analyst: read the fixture's transfer curve,
 * product margins and NMD behaviouralisation, then run the FTP scenario set —
 * the baseline, a +200bp shift of the whole curve and a +100bp funding-spread
 * stress — and read each persisted run back on the Ex-ante vs Ex-post tab.
 *
 * The expectations are the FIXTURE's, not the screen's. The curve and the
 * product book are `tests/fixtures/canonical_bank_fixture.py` `_FTP_CURVE_POINTS`
 * and `_FTP_PRODUCTS`; the spec prices each product against the curve with the
 * engine's match-funded margin (asset: customer − FTP − opex − ECL − capital;
 * funding: FTP − customer − opex), sums contributions itself, and moves the
 * curve itself for each stress overlay. The book is carried forward unchanged
 * to the latest month end, so none of these figures move with the calendar.
 */
import { expect, test } from "@playwright/test";
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

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** Base yield + liquidity premium + funding spread = FTP rate, per tenor. */
const CURVE = [
  { tenor: "overnight", years: 0.003, base: 25.0, lpBps: 0, spreadBps: 40 },
  { tenor: "91d", years: 0.25, base: 25.4, lpBps: 0, spreadBps: 45 },
  { tenor: "182d", years: 0.5, base: 26.1, lpBps: 5, spreadBps: 45 },
  { tenor: "1y", years: 1.0, base: 27.0, lpBps: 10, spreadBps: 50 },
  { tenor: "2y", years: 2.0, base: 27.8, lpBps: 20, spreadBps: 50 },
  { tenor: "3y", years: 3.0, base: 28.4, lpBps: 30, spreadBps: 55 },
  { tenor: "5y", years: 5.0, base: 28.9, lpBps: 40, spreadBps: 55 },
  { tenor: "10y", years: 10.0, base: 29.5, lpBps: 50, spreadBps: 60 },
].map((p) => ({ ...p, ftp: p.base + (p.lpBps + p.spreadBps) / 100 }));

function ftpAt(years: number): number {
  const point = CURVE.find((p) => p.years === years);
  if (!point) throw new Error(`the fixture curve has no ${years}y point`);
  return point.ftp;
}

type Product = {
  name: string;
  book: "Asset" | "Liability";
  balance: number;
  years: number;
  customer: number;
  opex: number;
  ecl: number;
  capital: number;
};

/** The product book, GHS millions and percent, priced at its tenor's FTP rate. */
const PRODUCTS: Product[] = [
  {
    name: "Corporate 5y",
    book: "Asset",
    balance: 560,
    years: 5,
    customer: 32.0,
    opex: 0.5,
    ecl: 0.8,
    capital: 0.15,
  },
  {
    name: "Sme 2y",
    book: "Asset",
    balance: 280,
    years: 2,
    customer: 33.0,
    opex: 0.7,
    ecl: 1.2,
    capital: 0.11,
  },
  {
    name: "Mortgage 10y",
    book: "Asset",
    balance: 200,
    years: 10,
    customer: 30.0,
    opex: 0.4,
    ecl: 0.3,
    capital: 0.05,
  },
  {
    name: "Retail 1y",
    book: "Asset",
    balance: 250,
    years: 1,
    customer: 34.0,
    opex: 0.9,
    ecl: 1.5,
    capital: 0.08,
  },
  {
    name: "Gov Securities 3y",
    book: "Asset",
    balance: 620,
    years: 3,
    customer: 27.5,
    opex: 0.05,
    ecl: 0,
    capital: 0,
  },
  {
    name: "Current Accounts",
    book: "Liability",
    balance: 700,
    years: 0.003,
    customer: 0.0,
    opex: 0.3,
    ecl: 0,
    capital: 0,
  },
  {
    name: "Savings",
    book: "Liability",
    balance: 300,
    years: 0.5,
    customer: 8.5,
    opex: 0.4,
    ecl: 0,
    capital: 0,
  },
  {
    name: "Term 3m",
    book: "Liability",
    balance: 280,
    years: 0.25,
    customer: 21.5,
    opex: 0.2,
    ecl: 0,
    capital: 0,
  },
  {
    name: "Term 1y",
    book: "Liability",
    balance: 220,
    years: 1,
    customer: 23.4,
    opex: 0.2,
    ecl: 0,
    capital: 0,
  },
  {
    name: "Wholesale",
    book: "Liability",
    balance: 240,
    years: 0.25,
    customer: 23.0,
    opex: 0.1,
    ecl: 0,
    capital: 0,
  },
];

/** Net match-funded margin, percent, with the whole curve moved by `shiftPct`. */
function margin(p: Product, shiftPct = 0): number {
  const ftp = ftpAt(p.years) + shiftPct;
  return p.book === "Asset"
    ? p.customer - ftp - p.opex - p.ecl - p.capital
    : ftp - p.customer - p.opex;
}

/** Portfolio figures for the book with the curve moved by `shiftPct`. */
function portfolio(shiftPct: number) {
  const contribution = (p: Product) => (p.balance * margin(p, shiftPct)) / 100;
  const assets = PRODUCTS.filter((p) => p.book === "Asset");
  const funding = PRODUCTS.filter((p) => p.book === "Liability");
  const balance = (ps: Product[]) => sum(ps.map((p) => p.balance));
  const total = sum(PRODUCTS.map(contribution));
  return {
    nimPct: (total / balance(PRODUCTS)) * 100,
    assetYieldPct: (sum(assets.map(contribution)) / balance(assets)) * 100,
    fundingCreditPct: (sum(funding.map(contribution)) / balance(funding)) * 100,
    contribution: total,
    belowFloor: PRODUCTS.filter((p) => margin(p, shiftPct) < 0).length,
  };
}

/** The governed overlays each scenario applies to the curve, percent. */
const SCENARIOS = [
  { code: "baseline", shiftPct: 0, curveShift: "None" },
  { code: "rates_up_200", shiftPct: 2, curveShift: "2.00%" },
  { code: "funding_stress", shiftPct: 1, curveShift: "1.00%" },
];

/** Core / volatile split; the core leg is priced at its effective duration. */
const NMD = [
  {
    segment: "Current Accounts",
    balance: 700,
    corePct: 65,
    durationYears: 2.5,
  },
  { segment: "Savings", balance: 300, corePct: 70, durationYears: 3.0 },
].map((s) => {
  const coreFtp = interpolate(s.durationYears);
  const volatileFtp = ftpAt(0.003);
  return {
    ...s,
    coreFtp,
    volatileFtp,
    assigned: (s.corePct * coreFtp + (100 - s.corePct) * volatileFtp) / 100,
  };
});

test.describe("FTP functional workflow", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("reads the fixture's curve, margins and NMD, runs the overlays and reads them ex-ante", async ({
    page,
  }) => {
    test.setTimeout(120_000);
    const base = portfolio(0);
    // Hand-checkable anchors: 262.652M contribution on a 3,650M book.
    expect(base.contribution).toBeCloseTo(262.652, 9);
    expect(base.belowFloor).toBe(2);

    // ---- Curve: every tenor's build-up and the portfolio KPIs.
    await page.goto("/ftp");
    await expect(
      page.getByRole("heading", { name: "Transfer Curve" }),
    ).toBeVisible();
    const blended =
      sum(NMD.map((s) => s.balance * s.assigned)) /
      sum(NMD.map((s) => s.balance));
    await expectKpi(
      page,
      "Blended assigned FTP",
      pct(blended),
      "Balance-weighted across NMD segments",
    );
    await expectKpi(page, "Portfolio NIM (FTP-adjusted)", pct(base.nimPct));
    await expectKpi(
      page,
      "Weighted asset yield",
      pct(base.assetYieldPct),
      "Customer rate net of FTP, asset books",
    );
    await expectKpi(
      page,
      "Weighted funding credit",
      pct(base.fundingCreditPct),
    );
    const curve = section(page, "Curve points").getByRole("table");
    for (const p of CURVE) {
      await expectRow(curve, p.tenor, [
        p.tenor,
        p.years.toFixed(2),
        pct(p.base),
        `${p.lpBps} bp`,
        `${p.spreadBps} bp`,
        pct(p.ftp),
      ]);
    }

    // ---- Product Profitability: each product's margin and contribution.
    await page
      .getByRole("link", { name: "Product Profitability", exact: true })
      .click();
    await expectKpi(
      page,
      "Total net contribution",
      ghsM(base.contribution),
      `Across ${ghsM(3650)} of balances`,
    );
    await expectKpi(
      page,
      "Products below floor",
      `${base.belowFloor} of ${PRODUCTS.length}`,
    );
    const products = section(page, "Product margin detail").getByRole("table");
    for (const p of PRODUCTS) {
      await expectRow(products, p.name, [
        p.name,
        p.book,
        ghsM(p.balance),
        pct(p.customer),
        pct(ftpAt(p.years)),
        pct(p.opex),
        pct(p.ecl),
        pct(p.capital),
        pct(margin(p)),
        signedGhsM((p.balance * margin(p)) / 100),
      ]);
    }

    // ---- Rules: the NMD behaviouralisation that sets the blended FTP.
    await page.getByRole("link", { name: "Rules", exact: true }).click();
    const nmd = section(page, "NMD behaviouralisation").getByRole("table");
    for (const s of NMD) {
      await expectRow(nmd, s.segment, [
        s.segment,
        ghsM(s.balance),
        `${s.corePct.toFixed(1)}%`,
        `${(100 - s.corePct).toFixed(1)}%`,
        `${s.durationYears.toFixed(2)}y`,
        pct(s.coreFtp),
        pct(s.volatileFtp),
        pct(s.assigned),
      ]);
    }

    // ---- Run the scenario set; each overlay moves the curve under the book.
    const runButton = page.getByRole("button", { name: "Run FTP scenarios" });
    await expect(runButton).toBeEnabled();
    const minted = page.waitForResponse(
      (r) =>
        r.url().endsWith("/ftp/run-all-scenarios") &&
        r.request().method() === "POST",
    );
    await runButton.click();
    const response = await minted;
    expect(response.status()).toBe(201);
    const batch = await response.json();
    expect(
      batch.runs.map((run: { scenario_code: string }) => run.scenario_code),
    ).toEqual(SCENARIOS.map((s) => s.code));
    for (const [i, scenario] of SCENARIOS.entries()) {
      const run = batch.runs[i];
      expect(run.status).toBe("succeeded");
      const persisted = await apiGet(
        page,
        "analyst",
        `/banks/${SAMPLE_BANK_ID}/regulatory-runs/${run.id}`,
      );
      expect(persisted.input_hash).toBe(run.input_hash);
      const expected = portfolio(scenario.shiftPct);
      expect(
        Number(persisted.metrics.total_contribution_ghs) / 1e6,
      ).toBeCloseTo(expected.contribution, 6);
      expect(persisted.metrics.products_below_min_margin).toBe(
        expected.belowFloor,
      );
    }

    // ---- Ex-ante vs Ex-post: the persisted runs, scenario by scenario.
    await page
      .getByRole("link", { name: "Ex-ante vs Ex-post", exact: true })
      .click();
    const stressNims = SCENARIOS.slice(1).map(
      (s) => portfolio(s.shiftPct).nimPct,
    );
    await expectKpi(
      page,
      "Ex-ante baseline NIM",
      pct(base.nimPct),
      "Stored baseline for this period",
    );
    await expectKpi(
      page,
      "Worst ex-ante stress NIM",
      pct(Math.min(...stressNims)),
    );
    // The dashboard reads the latest period, so no later period measures it yet.
    await expectKpi(
      page,
      "Ex-post stand-in NIM",
      "—",
      "No subsequent period measured yet",
    );
    const comparison = section(page, "Scenario comparison").getByRole("table");
    const byScenario = SCENARIOS.map((s) => portfolio(s.shiftPct));
    const rows: [
      string,
      (p: ReturnType<typeof portfolio>, i: number) => string,
    ][] = [
      ["Portfolio NIM", (p) => pct(p.nimPct)],
      ["Weighted asset yield", (p) => pct(p.assetYieldPct)],
      ["Weighted funding credit", (p) => pct(p.fundingCreditPct)],
      ["Total contribution", (p) => ghsM(p.contribution)],
      ["Products below floor", (p) => String(p.belowFloor)],
      ["Curve shift applied", (_p, i) => SCENARIOS[i].curveShift],
    ];
    for (const [metric, render] of rows) {
      await expectRow(comparison, metric, [
        metric,
        ...byScenario.map(render),
        "—",
      ]);
    }

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "ftp-ex-ante.png"),
        fullPage: true,
      });
    }
  });
});

/** Linear interpolation on the fixture curve, as the engine prices a duration. */
function interpolate(years: number): number {
  const upper = CURVE.findIndex((p) => p.years >= years);
  const hi = CURVE[upper];
  if (hi.years === years || upper === 0) return hi.ftp;
  const lo = CURVE[upper - 1];
  return (
    lo.ftp + ((hi.ftp - lo.ftp) * (years - lo.years)) / (hi.years - lo.years)
  );
}

function pct(value: number): string {
  return `${value.toFixed(2)}%`;
}

function sum(values: number[]): number {
  return values.reduce((t, v) => t + v, 0);
}
