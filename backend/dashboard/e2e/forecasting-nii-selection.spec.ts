/** Saved-run selection follows the persisted horizon, including custom runs. */
import { expect, test } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { writeFileSync } from "node:fs";
import path from "path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID, apiGet, expectKpi, section } from "./support/figures";
import { ghs, signedPct } from "./support/forecast";
import { mintBackendToken } from "./support/mint";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

test.describe("NII saved-run selection", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("reads one-, three-, seven- and ten-year custom runs without truncation or extra years", async ({
    page,
  }) => {
    test.setTimeout(150_000);
    const periods = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    );
    const token = await mintBackendToken("analyst");
    const saved: any[] = [];
    // Keep a five-year base reference so longer selected paths exercise the
    // missing comparison years too: absence is a dash, never an invented value.
    for (const horizon of [5, 1, 3, 7, 10]) {
      const response = await page.request.post(
        `${E2E_API_ORIGIN}/api/v1/banks/${SAMPLE_BANK_ID}/forecast/runs`,
        {
          headers: { Authorization: `Bearer ${token}` },
          data: {
            reporting_period_id: periods.periods[0].id,
            horizon_years: horizon,
            scenario_code: horizon === 5 ? "base" : "custom",
            ...(horizon === 5 ? {} : { assumptions: { nim_pct: "5.5" } }),
          },
        },
      );
      expect(response.status(), await response.text()).toBe(201);
      const run = await response.json();
      expect(run.status).toBe("succeeded");
      saved.push(run);
    }

    for (const run of saved.slice(1)) {
      const points = run.path.filter((point: any) => point.year > 0);
      const horizon = points.length;
      await page.goto(`/forecasting/nii?run=${run.id}`);
      await expect(page.getByText(/^Reading run/)).toContainText(
        `${run.id.slice(0, 8)} — Custom scenario · ${horizon}-year horizon`,
      );
      await expect(page.getByLabel("Forecast run")).toHaveValue(run.id);
      await expectKpi(page, "Y1 projected NII", ghs(Number(points[0].nii)));
      await expectKpi(
        page,
        `${horizon}-year cumulative NII`,
        ghs(
          points.reduce(
            (total: number, point: any) => total + Number(point.nii),
            0,
          ),
        ),
      );
      await expectKpi(
        page,
        `NII CAGR Y1→Y${horizon}`,
        horizon === 1
          ? "—"
          : signedPct(
              (Math.pow(
                Number(points[horizon - 1].nii) / Number(points[0].nii),
                1 / (horizon - 1),
              ) -
                1) *
                100,
            ),
      );
      const rows = section(page, "Sensitivity vs base").locator("tbody tr");
      await expect(rows).toHaveCount(horizon);
      for (const [i, point] of points.entries()) {
        await expect(rows.nth(i).locator("td").first()).toHaveText(
          `Y${point.year}`,
        );
        await expect(rows.nth(i).locator("td").last()).toContainText(
          ghs(Number(point.nii)),
        );
        if (point.year > 5) {
          await expect(rows.nth(i).locator("td").nth(1)).toHaveText("—");
        }
      }
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, `forecasting-nii-${horizon}-year.png`),
          fullPage: true,
        });
      }
    }

    // The visible picker must replace the deep-linked run, including its labels.
    await page.getByLabel("Forecast run").selectOption(saved[2].id);
    await expect(page.getByText(/^Reading run/)).toContainText(
      `${saved[2].id.slice(0, 8)} — Custom scenario · 3-year horizon`,
    );
    await expect(
      section(page, "Sensitivity vs base").locator("tbody tr"),
    ).toHaveCount(3);

    // An invalid requested id must surface the error, not silently read base.
    await page.goto(`/forecasting/nii?run=${randomUUID()}`);
    await expect(
      page.getByText("Could not load data", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/^Reading run/)).toHaveCount(0);
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-nii-missing-run.png"),
        fullPage: true,
      });
      writeFileSync(
        path.join(evidenceDir, "forecasting-nii-horizon-runs.json"),
        JSON.stringify(saved, null, 2),
      );
    }
  });
});
