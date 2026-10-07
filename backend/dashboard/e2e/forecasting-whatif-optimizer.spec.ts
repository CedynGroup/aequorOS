// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * What-if Lab and Strategy Optimizer, worked end to end by an analyst: run two
 * stylized shocks and read the shocked path against the base, then run the
 * optimizer's deterministic decision grid and read its ranking, and confirm
 * every result projects the SAME facts, as-of date and governed parameters as
 * a saved base forecast.
 *
 * The expectations are the FIXTURE's, not the screen's: `support/forecast.ts`
 * projects the canonical book under the approved base preset, applies each
 * shock's fixed adjustment (`WHATIF_SHOCKS` in
 * `app/domain/forecasting/engine.py`) and each optimizer decision itself, and
 * derives net income, ROE and CAR from them. The optimizer's feasibility is
 * the derived CAR path against the governed 13% floor; LCR and NSFR never bind
 * on this book, and their paths are pinned as engine output.
 */
import { expect, test, type Page } from "@playwright/test";
import path from "path";
import { writeFileSync } from "node:fs";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID, apiGet, expectKpi, section } from "./support/figures";
import {
  BASE,
  CAR_MIN_PCT,
  ghs,
  optimizerGrid,
  pct,
  project,
  withDecision,
  type Assumptions,
  type Decision,
} from "./support/forecast";
import { mintBackendToken } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** Each shock's library label, its fixed adjustment, and what it moves. */
const SHOCKS = [
  {
    label: "Policy rate cut −200bps",
    shocked: {
      ...BASE,
      nimPct: BASE.nimPct - 0.4,
      loanGrowthPct: BASE.loanGrowthPct + 4,
    },
    stored: { nim_delta: "-0.4", loan_growth_delta: "4" },
    moved: [
      ["Loan growth", BASE.loanGrowthPct, BASE.loanGrowthPct + 4],
      ["Net interest margin", BASE.nimPct, BASE.nimPct - 0.4],
    ],
  },
  {
    label: "Loan default spike (2.5× credit losses)",
    shocked: { ...BASE, creditLossRatePct: BASE.creditLossRatePct * 2.5 },
    stored: { credit_loss_multiplier: "2.5" },
    moved: [
      [
        "Credit loss rate",
        BASE.creditLossRatePct,
        BASE.creditLossRatePct * 2.5,
      ],
    ],
  },
] satisfies {
  label: string;
  shocked: Assumptions;
  stored: Record<string, string>;
  moved: [string, number, number][];
}[];

test.describe("Forecasting What-if Lab and Optimizer", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("shocks and the decision grid project the saved base forecast's facts and policy", async ({
    page,
  }) => {
    test.setTimeout(180_000);
    const base = project(BASE);
    const periods = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    );
    const period = periods.periods[0];
    // The reference every result must share: a saved base forecast.
    const saved = await apiPost(page, "/forecast/runs", {
      reporting_period_id: period.id,
      scenario_code: "base",
    });
    const reference = await stored(page, saved.id);

    // ---- What-if Lab: each stylized shock against the deterministic base.
    await page.goto("/forecasting/whatif");
    await expect(section(page, "Shock library")).toContainText(
      "Four stylized shocks — fixed adjustments to the base assumptions",
    );
    for (const shock of SHOCKS) {
      const shocked = project(shock.shocked);
      const card = page.getByRole("button", {
        name: new RegExp(`^${escape(shock.label)}`),
      });
      await card.click();
      await expect(card).toHaveAttribute("aria-pressed", "true");
      const ran = page.waitForResponse(
        (r) =>
          r.url().endsWith(`/banks/${SAMPLE_BANK_ID}/forecast/whatif`) &&
          r.request().method() === "POST",
      );
      await page
        .getByRole("button", {
          name: new RegExp(`^(Re-run|Run) ${escape(shock.label)}$`),
        })
        .click();
      const response = await ran;
      expect(response.status()).toBe(201);
      const result = await response.json();
      expect(result.status).toBe("succeeded");
      expect(result.reporting_period_id).toBe(period.id);
      expect(result.assumption_version).toEqual(saved.assumption_version);
      await expect(
        page.getByText(
          new RegExp(
            `^Assumptions: Version ${saved.assumption_version.version_number} · approved by`,
          ),
        ),
      ).toContainText(saved.assumption_version.approved_by_name);

      // The library judges the lowest shocked CAR against the governed floor.
      await expect(card).toContainText(
        shocked.summary.minCarPct < CAR_MIN_PCT
          ? "CAR breach"
          : "CAR clears floor",
      );
      const carDelta = shocked.summary.finalCarPct - base.summary.finalCarPct;
      await expect(card).toContainText(
        `Y5 CAR ${pct(shocked.summary.finalCarPct)} ${badge(carDelta, " pp", 2)}`,
      );

      // Year-5 comparison, the per-year impact and what the shock moved.
      await expectComparison(page, "Y5 CAR", pct(shocked.summary.finalCarPct), [
        pct(base.summary.finalCarPct),
        badge(carDelta, " pp", 2),
      ]);
      const [b5, s5] = [base.years[5].netIncome, shocked.years[5].netIncome];
      await expectComparison(page, "Y5 net income", ghs(s5), [
        ghs(b5),
        `${s5 - b5 >= 0 ? "+" : "-"}${ghs(Math.abs(s5 - b5))}`,
      ]);
      const impact = section(page, "Impact vs base by year").getByRole("table");
      for (let year = 1; year <= 5; year += 1) {
        const storedDelta = result.deltas.find(
          (d: { year: number }) => d.year === year,
        );
        const niDelta =
          shocked.years[year].netIncome - base.years[year].netIncome;
        await expect(
          impact
            .locator("tbody tr")
            .nth(year - 1)
            .locator("td"),
        ).toHaveText([
          `Y${year}`,
          signed(shocked.years[year].carPct - base.years[year].carPct),
          // LCR and NSFR are engine output: the table shows the stored deltas.
          signed(Number(storedDelta.lcr_delta_pp)),
          signed(Number(storedDelta.nsfr_delta_pp)),
          `${niDelta >= 0 ? "+" : "-"}${ghs(Math.abs(niDelta))}`,
        ]);
      }
      const moved = section(page, "What the shock moved").getByRole("table");
      await expect(moved.locator("tbody tr")).toHaveCount(shock.moved.length);
      for (const [i, [label, from, to]] of shock.moved.entries()) {
        await expect(moved.locator("tbody tr").nth(i).locator("td")).toHaveText(
          [
            label,
            `${from.toFixed(2)}%`,
            `${to.toFixed(2)}%`,
            badge(to - from, "%", 2),
          ],
        );
      }

      // Persisted: the shock as stored, over the saved forecast's inputs.
      const run = await stored(page, result.run_id);
      expect(run.inputs.shock).toEqual(shock.stored);
      expectSameInputs(run, reference);
      expect(Number(result.base_summary.year5_car_pct)).toBeCloseTo(
        base.summary.finalCarPct,
        6,
      );
      expect(result.base_summary.year5_lcr_pct).toBe(
        reference.metrics.year5_lcr_pct,
      );
    }
    await page.reload();
    // Reload resets the selected shock; choose a saved result before reading it.
    await page
      .getByRole("button", {
        name: new RegExp(`^${escape(SHOCKS[1].label)}`),
      })
      .click();
    await expect(
      page.getByText(
        new RegExp(
          `^Assumptions: Version ${saved.assumption_version.version_number} · approved by`,
        ),
      ),
    ).toContainText(saved.assumption_version.approved_by_name);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-whatif-provenance.png"),
        fullPage: true,
      });
    }

    // ---- Optimizer: the 108-point grid, ranked by derived average ROE.
    await page.goto("/forecasting/optimizer");
    const searched = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/banks/${SAMPLE_BANK_ID}/forecast/optimizer`) &&
        r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Run optimizer" }).first().click();
    const response = await searched;
    expect(response.status()).toBe(201);
    const search = await response.json();
    expect(search.status).toBe("succeeded");
    expect(search.assumption_version).toEqual(saved.assumption_version);
    await expect(
      page.getByText(
        new RegExp(
          `^Assumptions: Version ${saved.assumption_version.version_number} · approved by`,
        ),
      ),
    ).toContainText(saved.assumption_version.approved_by_name);

    const candidates = optimizerGrid().map((decision) => ({
      decision,
      summary: project(withDecision(BASE, decision)).summary,
    }));
    const feasible = candidates
      .filter((c) => c.summary.minCarPct >= CAR_MIN_PCT)
      .sort(
        (a, b) =>
          b.summary.avgRoePct - a.summary.avgRoePct ||
          tiebreak(a.decision, b.decision),
      );
    // LCR and NSFR never bind on this book; CAR alone eliminates candidates.
    expect(search.binding_constraint_histogram).toEqual({
      car: candidates.length - feasible.length,
      lcr: 0,
      nsfr: 0,
    });
    const top = feasible.slice(0, 10);

    await expectKpi(page, "Candidates evaluated", String(candidates.length));
    await expectKpi(
      page,
      "Feasible strategies",
      String(feasible.length),
      `of ${candidates.length} cleared every capital and liquidity floor`,
    );
    await expectKpi(page, "Best 5Y average ROE", pct(top[0].summary.avgRoePct));
    const ranking = section(page, "Full ranking").getByRole("table");
    await expect(ranking.locator("tbody tr")).toHaveCount(top.length);
    for (const [i, { decision: d, summary }] of top.entries()) {
      const storedCandidate = search.top[i];
      await expect(ranking.locator("tbody tr").nth(i).locator("td")).toHaveText(
        [
          i === 0 ? "#1Recommended" : `#${i + 1}`,
          pct(d.loanGrowthPct, 1),
          `${d.securitiesShiftPp >= 0 ? "+" : ""}${d.securitiesShiftPp.toFixed(1)} pp`,
          `+${d.depositPremiumBps} bps`,
          pct(d.dividendPayoutPct, 0),
          pct(summary.avgRoePct),
          pct(summary.finalCarPct),
          pct(Number(storedCandidate.summary.year5_lcr_pct), 1),
          pct(Number(storedCandidate.summary.year5_nsfr_pct), 1),
          "CARLCRNSFR",
        ],
      );
    }
    // The method note names the floors the search enforced, from the run.
    await expect(section(page, "How the optimizer works")).toContainText(
      `Deterministic grid search: every one of the 108 decision combinations`,
    );
    await expect(section(page, "How the optimizer works")).toContainText(
      `every year holds CAR ≥ ${pct(CAR_MIN_PCT, 1)}, LCR ≥ 100.0%, NSFR ≥ 100.0%`,
    );

    const optimizerRun = await stored(page, search.run_id);
    expect(optimizerRun.scenario_code).toBe("constrained_search");
    expectSameInputs(optimizerRun, reference);
    expect(optimizerRun.metrics.candidates_evaluated).toBe(candidates.length);
    await page.reload();
    await expect(
      page.getByText(
        new RegExp(
          `^Assumptions: Version ${saved.assumption_version.version_number} · approved by`,
        ),
      ),
    ).toContainText(saved.assumption_version.approved_by_name);

    if (evidenceDir) {
      writeFileSync(
        path.join(evidenceDir, "forecasting-optimizer-provenance.json"),
        JSON.stringify(
          { reference: saved, search, persisted: optimizerRun },
          null,
          2,
        ),
      );
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-optimizer.png"),
        fullPage: true,
      });
    }
  });
});

/** The engine's tie-break after average ROE: the decision levers, ascending. */
function tiebreak(a: Decision, b: Decision): number {
  return (
    a.loanGrowthPct - b.loanGrowthPct ||
    a.securitiesShiftPp - b.securitiesShiftPp ||
    a.depositPremiumBps - b.depositPremiumBps ||
    a.dividendPayoutPct - b.dividendPayoutPct
  );
}

/** Same book, same as-of date, same governed parameters and resolved base. */
function expectSameInputs(run: StoredRun, reference: StoredRun) {
  expect(run.reporting_period_id).toBe(reference.reporting_period_id);
  expect(run.inputs.as_of_date).toBe(reference.inputs.as_of_date);
  expect(run.inputs.facts).toEqual(reference.inputs.facts);
  expect(run.inputs.parameters).toEqual(reference.inputs.parameters);
  expect(run.inputs.assumptions).toEqual(reference.inputs.assumptions);
}

/** A Y5 comparison cell: shocked value, then the base and its delta. */
async function expectComparison(
  page: Page,
  label: string,
  shocked: string,
  caption: string[],
) {
  const cell = page
    .locator("div.card")
    .filter({ has: page.locator("p", { hasText: new RegExp(`^${label}$`) }) });
  await expect(cell).toHaveCount(1);
  await expect(cell.locator("p.font-mono")).toHaveText(shocked);
  await expect(cell.locator("p").last()).toHaveText(`base ${caption.join("")}`);
}

type StoredRun = {
  scenario_code: string;
  reporting_period_id: string;
  inputs: Record<string, any>;
  metrics: Record<string, any>;
};

async function stored(page: Page, runId: string): Promise<StoredRun> {
  return apiGet(
    page,
    "analyst",
    `/banks/${SAMPLE_BANK_ID}/regulatory-runs/${runId}`,
  );
}

async function apiPost(page: Page, pathName: string, body: unknown) {
  const response = await page.request.post(
    `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}${pathName}`,
    {
      headers: { Authorization: `Bearer ${await mintBackendToken("analyst")}` },
      data: body,
    },
  );
  expect(response.status(), await response.text()).toBe(201);
  return response.json();
}

/** The what-if impact table's signed two-decimal delta. */
function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(2)}`;
}

/** `components/ui/DeltaBadge` text: glyph, sign, fixed decimals, suffix. */
function badge(value: number, suffix: string, decimals: number): string {
  const glyph = value > 0 ? "▲" : value < 0 ? "▼" : "";
  return `${glyph}${value > 0 ? "+" : ""}${value.toFixed(decimals)}${suffix}`;
}

function escape(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
