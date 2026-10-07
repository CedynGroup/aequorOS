// Set E2E_EVIDENCE_DIR to write reviewer-visible screenshots outside version control.
/**
 * Forecasting assumptions and the NII forecast, worked end to end: an analyst
 * reads the live baseline, saves the approved adverse and base projections,
 * reads the NII forecast and the Assumption Registry they feed, then edits two
 * assumptions in the Scenario designer and follows the one-off edited
 * projection through the run comparison, the Balance Sheet and the NII tab.
 *
 * Then the GOVERNED edit: the analyst drafts and submits a new version of the
 * approved assumption set, a different person (the approver) approves it, and
 * the next base projection and its NII forecast resolve the new version and
 * name it — who approved it, when, and from which book date — while every run
 * saved before the approval stays exactly as it was persisted.
 *
 * The expectations are the FIXTURE's, not the screen's: `support/forecast.ts`
 * projects the canonical book under the fixture's approved presets (version 1)
 * and under each edit, so every balance, NII, net income, ROE and CAR figure
 * here is derived, not read back. LCR and NSFR paths are engine output, pinned
 * as the same values on every surface.
 */
import {
  expect,
  test,
  type Browser,
  type Locator,
  type Page,
} from "@playwright/test";
import path from "path";
import { writeFileSync } from "node:fs";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID, apiGet, expectKpi, section } from "./support/figures";
import {
  ADVERSE,
  BASE,
  CAR_MIN_PCT,
  apiAssumptions,
  ghs,
  pct,
  project,
  signedPct,
  sum,
  type Assumptions,
  type Projection,
} from "./support/forecast";
import { mintBackendToken } from "./support/mint";
import { openTab } from "./support/navigation";

const evidenceDir = process.env.E2E_EVIDENCE_DIR;

/** The edit: a wider margin and faster loan growth on top of the base preset. */
const EDITED: Assumptions = { ...BASE, nimPct: 5.5, loanGrowthPct: 22 };

/** The governed revision of the base preset that version 2 approves. */
const GOVERNED: Assumptions = { ...BASE, nimPct: 5.2, loanGrowthPct: 20 };

const ASSUMPTIONS_PATH = `/banks/${SAMPLE_BANK_ID}/forecast/assumption-versions`;

/** Year-0 LCR: 735M of Level 1 HQLA over 499M of net 30-day outflows. */
const YEAR0_LCR_PCT = (735 / 499) * 100;

type AssumptionVersion = {
  id: string;
  version_number: number;
  status: string;
  presets: Record<string, Record<string, string>>;
};

type ForecastRun = {
  id: string;
  status: string;
  scenario_code: string;
  input_hash: string;
  assumptions: Record<string, string>;
  assumption_version: {
    version_number: number;
    approved_by_name: string | null;
    effective_from: string;
  } | null;
  summary: Record<string, string>;
  path: Record<string, string | number | null>[];
};

test.describe("Forecasting assumptions and NII", () => {
  test.use({ storageState: path.join(E2E_TMP, "analyst.json") });

  test("approved presets project the NII forecast; edits re-project it and leave saved runs unchanged", async ({
    page,
    browser,
  }) => {
    test.setTimeout(300_000);
    const base = project(BASE);
    const adverse = project(ADVERSE);
    const edited = project(EDITED);
    const governed = project(GOVERNED);
    // Hand-checkable anchors: 105.3888M of Y1 NII on the 2,020M earning book.
    expect(base.years[1].nii).toBe(105_388_800);
    expect(base.years[0].carPct).toBeCloseTo((340 / 2147.5) * 100, 5);

    const periods = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/reporting-periods`,
    );
    const period = periods.periods[0];

    // ---- Balance Sheet: the live baseline the approved base preset projects.
    await page.goto("/forecasting");
    const live = section(page, "Current live forecast baseline");
    await expect(live).toBeVisible();
    await expectKpi(live, "Year-5 CAR", pct(base.summary.finalCarPct));
    await expectKpi(live, "Average ROE", pct(base.summary.avgRoePct));

    // ---- Save the approved adverse and base projections.
    const adverseRun = await runPreset(page, "Adverse", "adverse");
    expectRunMatches(adverseRun, ADVERSE, adverse);
    const baseRun = await runPreset(page, "Base case", "base");
    expectRunMatches(baseRun, BASE, base);
    await expect(page.getByText("Base case scenario")).toBeVisible();
    await expect(
      page.getByText(/^Adverse band overlaid from run/),
    ).toContainText(adverseRun.id.slice(0, 8));
    await expectRunDashboard(page, base);

    // The live baseline and the saved run project the same facts under the
    // same policy: every headline figure, LCR and NSFR included, agrees.
    const summary = await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/live-summary`,
    );
    const liveForecast = summary.modules.find(
      (m: { module: string }) => m.module === "forecast",
    );
    for (const key of Object.keys(baseRun.summary)) {
      expect(liveForecast.metrics[key]).toBe(baseRun.summary[key]);
    }

    // What the platform kept: the run, its snapshot and its period.
    const savedBase = await persisted(page, baseRun.id);
    expect(savedBase.run.input_hash).toBe(baseRun.input_hash);
    expect(savedBase.snapshot.inputs.as_of_date).toBe(period.period_end);
    expect(savedBase.snapshot.inputs.reporting_period.id).toBe(period.id);
    expect(savedBase.snapshot.inputs.assumption_overrides).toBeNull();

    // ---- NII Forecast: the base run's NII path against the adverse run.
    await openTab(page, "NII Forecast");
    const baseNii = base.years.slice(1).map((y) => y.nii);
    await expectKpi(
      page,
      "Y1 projected NII",
      ghs(baseNii[0]),
      "Base case scenario",
    );
    await expectKpi(page, "5-year cumulative NII", ghs(sum(baseNii)));
    await expectKpi(
      page,
      "NII CAGR Y1→Y5",
      signedPct((Math.pow(baseNii[4] / baseNii[0], 1 / 4) - 1) * 100),
    );
    await expectKpi(page, "NIM assumption", pct(BASE.nimPct));
    const sensitivity = section(page, "Sensitivity vs base").getByRole("table");
    for (let year = 1; year <= 5; year += 1) {
      const cells = sensitivity
        .locator("tbody tr")
        .nth(year - 1)
        .locator("td");
      const [baseNii, adverseNii] = [
        base.years[year].nii,
        adverse.years[year].nii,
      ];
      await expect(cells.nth(0)).toHaveText(`Y${year}`);
      await expect(cells.nth(1)).toHaveText(ghs(baseNii));
      // Columns follow the preset order, so Adverse is the first comparison.
      await expect(cells.nth(2)).toHaveText(
        `${ghs(adverseNii)}${delta(((adverseNii - baseNii) / baseNii) * 100, "%", 1)}`,
      );
    }

    // ---- Assumptions: the approved preset set the base run resolved.
    await openTab(page, "Assumptions");
    const resolved = section(page, "Resolved on the latest run");
    await expect(resolved).toContainText("Base case run");
    await expectResolved(resolved, BASE, {
      sourceOf: (label) =>
        ["Fee income", "Tax rate", "Securities shift"].includes(label)
          ? "Engine default"
          : "Base case preset",
    });
    const catalogue = section(page, "Preset catalogue").getByRole("table");
    await expectCatalogueRow(catalogue, "Loan growth", [
      "18%",
      "8%",
      "-2%",
      "—",
    ]);
    await expectCatalogueRow(catalogue, "Net interest margin", [
      "4.8%",
      "4.2%",
      "3.6%",
      "—",
    ]);
    await expectCatalogueRow(catalogue, "Fee income", ["—", "—", "—", "1.2%"]);
    await expectCatalogueRow(catalogue, "Securities shift", [
      "—",
      "—",
      "—",
      "0 pp",
    ]);

    // ---- Scenarios: edit two assumptions on top of the base preset and save.
    await openTab(page, "Scenarios");
    const designer = section(page, "Scenario designer");
    await designer
      .getByLabel("Net interest margin value", { exact: true })
      .fill(String(EDITED.nimPct));
    await designer
      .getByLabel("Loan growth value", { exact: true })
      .fill(String(EDITED.loanGrowthPct));
    await expect(designer.getByText("Custom — 2 of 10 changed")).toBeVisible();
    const editedRun = await saveRun(page, designer, "Run custom scenario");
    expect(editedRun.scenario_code).toBe("custom");
    expectRunMatches(editedRun, EDITED, edited);
    await expect(designer.getByText("Run succeeded")).toBeVisible();
    await expectKpi(designer, "Average ROE", pct(edited.summary.avgRoePct));
    await expectKpi(designer, "Year-5 CAR", pct(edited.summary.finalCarPct));
    await expectKpi(
      designer,
      "Year-5 LCR",
      pct(Number(editedRun.summary.year5_lcr_pct), 1),
    );
    await expectKpi(
      designer,
      "Cumulative net income",
      ghs(edited.summary.cumulativeNetIncome),
    );

    // The edit is its own snapshot over the SAME facts and policy, and the
    // earlier saved base run is byte-for-byte what was persisted.
    const savedEdit = await persisted(page, editedRun.id);
    expect(savedEdit.run.input_hash).not.toBe(baseRun.input_hash);
    expect(savedEdit.snapshot.inputs.facts).toEqual(
      savedBase.snapshot.inputs.facts,
    );
    expect(savedEdit.snapshot.inputs.parameters).toEqual(
      savedBase.snapshot.inputs.parameters,
    );
    expect(savedEdit.snapshot.inputs.as_of_date).toBe(period.period_end);
    expect(savedEdit.snapshot.inputs.assumption_overrides).toMatchObject({
      nim_pct: "5.5",
      loan_growth_pct: "22",
    });
    expect(await persisted(page, baseRun.id)).toEqual(savedBase);

    // ---- Compare the saved base run (A) with the edit (B).
    await page
      .getByRole("radio", {
        name: `Compare run ${baseRun.id.slice(0, 8)} as A`,
      })
      .check();
    await page
      .getByRole("radio", {
        name: `Compare run ${editedRun.id.slice(0, 8)} as B`,
      })
      .check();
    const deltas = section(page, "Summary deltas").getByRole("table");
    await expectCells(deltas, "Average ROE", [
      pct(base.summary.avgRoePct),
      pct(edited.summary.avgRoePct),
      delta(edited.summary.avgRoePct - base.summary.avgRoePct, " pp", 2),
    ]);
    await expectCells(deltas, "Cumulative net income", [
      ghs(base.summary.cumulativeNetIncome),
      ghs(edited.summary.cumulativeNetIncome),
      delta(
        (edited.summary.cumulativeNetIncome -
          base.summary.cumulativeNetIncome) /
          1e6,
        "M GHS",
        1,
      ),
    ]);
    await page.getByLabel("Comparison metric").selectOption("NII");
    const perYear = section(page, "Per-year NII deltas").getByRole("table");
    for (let year = 1; year <= 5; year += 1) {
      await expectCells(perYear, `Y${year}`, [
        ghs(base.years[year].nii),
        ghs(edited.years[year].nii),
        delta(
          (edited.years[year].nii - base.years[year].nii) / 1e6,
          "M GHS",
          1,
        ),
      ]);
    }
    const diff = section(page, "Resolved assumption diff");
    await expect(diff).toContainText(
      "2 of 10 assumptions differ between the two runs.",
    );
    await expectCells(diff.getByRole("table"), "Net interest margin", [
      "4.8%",
      "5.5%",
      delta(0.7, "%", 1),
    ]);

    // ---- Open the edit on the Balance Sheet: its own NII path.
    await page.getByRole("link", { name: "Open on Balance Sheet" }).click();
    await page.waitForURL(`**/forecasting?run=${editedRun.id}`);
    await expect(page.getByText("Custom scenario")).toBeVisible();
    await expectRunDashboard(page, edited);

    // ---- The registry now resolves the edit, labelled as an override.
    await openTab(page, "Assumptions");
    await expect(section(page, "Resolved on the latest run")).toContainText(
      "Custom run",
    );
    await expectResolved(section(page, "Resolved on the latest run"), EDITED, {
      sourceOf: () => "Custom override",
    });

    // ---- The NII tab reads the edited run when it is the one chosen.
    await page.goto(`/forecasting/nii?run=${editedRun.id}`);
    await expect(page.getByText(/^Reading run/)).toContainText(
      `${editedRun.id.slice(0, 8)} — Custom scenario · 5-year horizon`,
    );
    await expectKpi(
      page,
      "Y1 projected NII",
      ghs(edited.years[1].nii),
      "Custom scenario",
    );
    const editedNii = edited.years.slice(1).map((y) => y.nii);
    await expectKpi(page, "5-year cumulative NII", ghs(sum(editedNii)));
    // The edited run joins the presets in the sensitivity table, against the
    // saved base run.
    const editedColumn = section(page, "Sensitivity vs base").getByRole(
      "table",
    );
    await expect(
      editedColumn.getByRole("columnheader", {
        name: `Custom run ${editedRun.id.slice(0, 8)} · Δ vs base`,
      }),
    ).toBeVisible();
    await expect(
      editedColumn.locator("tbody tr").first().locator("td").last(),
    ).toHaveText(
      `${ghs(edited.years[1].nii)}${delta(
        ((edited.years[1].nii - base.years[1].nii) / base.years[1].nii) * 100,
        "%",
        1,
      )}`,
    );
    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-custom-nii.png"),
        fullPage: true,
      });
    }

    // ---- The governed edit: draft, submit, an independent approval.
    const register = await apiGet(page, "analyst", ASSUMPTIONS_PATH);
    const approvedV1 = register.versions.find(
      (v: AssumptionVersion) => v.id === register.effective_version_id,
    ) as AssumptionVersion;
    expect(approvedV1.version_number).toBe(1);
    try {
      await openTab(page, "Assumptions");
      const inForce = section(page, "Approved assumptions in force");
      await expect(inForce).toContainText("Version 1");
      await expect(inForce).toContainText("Forecast Fixture Checker");
      await expect(section(page, "Preset catalogue")).toContainText(
        "Version 1 · approved by Forecast Fixture Checker",
      );

      await page.getByRole("button", { name: "Propose new version" }).click();
      const editor = section(page, "Propose new version");
      // Effective from the bank's latest book date by default.
      await expect(editor.getByLabel("Effective from")).toHaveValue(
        period.period_end,
      );
      await editor
        .getByLabel("Base case Net interest margin", { exact: true })
        .fill(String(GOVERNED.nimPct));
      await editor
        .getByLabel("Base case Loan growth", { exact: true })
        .fill(String(GOVERNED.loanGrowthPct));
      await editor
        .getByLabel("Change note")
        .fill("Board plan revision: wider margin, faster loan growth.");
      await editor.getByRole("button", { name: "Save draft" }).click();

      const pending = section(page, "Version 2 — pending change");
      await expect(pending).toContainText("Draft");
      await expect(pending).toContainText("drafted by E2E Analyst");
      await expect(pending).toContainText("5.2%(was 4.8%)");
      await pending
        .getByRole("button", { name: "Submit for approval" })
        .click();
      await expect(pending).toContainText("Awaiting approval");
      await expect(pending).toContainText("submitted by E2E Analyst");
      // The maker is offered no decision on their own submission.
      await expect(
        pending.getByRole("button", { name: "Approve" }),
      ).toHaveCount(0);
      // Not yet approved: version 1 is still in force.
      await expect(inForce).toContainText("Version 1");

      const submitted = await apiGet(page, "analyst", ASSUMPTIONS_PATH);
      const selfApproval = await page.request.post(
        `${E2E_API_ORIGIN}/api/v1${ASSUMPTIONS_PATH}/${submitted.open_version_id}/approve`,
        {
          data: {},
          headers: {
            Authorization: `Bearer ${await mintBackendToken("analyst")}`,
          },
        },
      );
      expect(selfApproval.status()).toBe(403);
      expect(
        (await apiGet(page, "analyst", ASSUMPTIONS_PATH)).effective_version_id,
      ).toBe(approvedV1.id);
      if (evidenceDir) {
        writeFileSync(
          path.join(evidenceDir, "forecasting-self-approval-refusal.json"),
          JSON.stringify(
            { status: selfApproval.status(), body: await selfApproval.json() },
            null,
            2,
          ),
        );
        await page.screenshot({
          path: path.join(evidenceDir, "forecasting-submitted-version.png"),
          fullPage: true,
        });
      }

      await approveAsApprover(
        browser,
        "Board minute 14: plan revision approved.",
      );

      await page.reload();
      await expect(inForce).toContainText("Version 2");
      await expect(inForce).toContainText("E2E Approver");
      await expect(inForce).toContainText(fmtUtcDate(period.period_end));
      const history = section(page, "Version history").getByRole("table");
      await expect(history.locator("tbody tr").first()).toContainText(
        "Version 2",
      );
      await expect(history.locator("tbody tr").first()).toContainText(
        "Approved",
      );
      await expect(section(page, "Preset catalogue")).toContainText(
        "Version 2 · approved by E2E Approver",
      );
      if (evidenceDir) {
        await page.screenshot({
          path: path.join(evidenceDir, "forecasting-approved-register.png"),
          fullPage: true,
        });
      }

      // ---- The next base projection resolves version 2 and names it.
      await page.goto("/forecasting");
      const governedRun = await runPreset(page, "Base case", "base");
      expectRunMatches(governedRun, GOVERNED, governed);
      expect(governedRun.assumption_version).toMatchObject({
        version_number: 2,
        approved_by_name: "E2E Approver",
        effective_from: period.period_end,
      });
      expect(governedRun.input_hash).not.toBe(baseRun.input_hash);
      await expect(
        page.getByText(/^Assumptions: Version 2 · approved by E2E Approver/),
      ).toBeVisible();
      await expectRunDashboard(page, governed);

      // ---- NII Forecast: the latest base run is the governed one.
      await openTab(page, "NII Forecast");
      await expect(page.getByText(/^Reading run/)).toContainText(
        `${governedRun.id.slice(0, 8)} — Base case scenario`,
      );
      await expect(page.getByText(/^Reading run/)).toContainText(
        "assumptions: Version 2 · approved by E2E Approver",
      );
      const governedNii = governed.years.slice(1).map((y) => y.nii);
      await expectKpi(
        page,
        "Y1 projected NII",
        ghs(governedNii[0]),
        "Base case scenario",
      );
      expect(governedNii[0]).not.toBe(baseNii[0]);
      await expectKpi(page, "5-year cumulative NII", ghs(sum(governedNii)));
      await expectKpi(page, "NIM assumption", pct(GOVERNED.nimPct));

      // ---- Every run saved before the approval is exactly what was persisted.
      expect(await persisted(page, baseRun.id)).toEqual(savedBase);
      expect(await persisted(page, editedRun.id)).toEqual(savedEdit);
      expect(
        (
          await apiGet(
            page,
            "analyst",
            `/banks/${SAMPLE_BANK_ID}/forecast/runs/${baseRun.id}`,
          )
        ).assumption_version.version_number,
      ).toBe(1);
      if (evidenceDir) {
        writeFileSync(
          path.join(evidenceDir, "forecasting-saved-run-immutability.json"),
          JSON.stringify(
            {
              before: { base: savedBase, custom: savedEdit },
              after: {
                base: await persisted(page, baseRun.id),
                custom: await persisted(page, editedRun.id),
              },
              governedRun,
              restoredPresets: approvedV1.presets,
            },
            null,
            2,
          ),
        );
      }
    } finally {
      // Later journeys project the fixture's version-1 figures: approve them
      // again as the newest version, whatever state this one stopped in.
      await restoreApprovedPresets(page, approvedV1.presets);
      const restored = await apiGet(page, "analyst", ASSUMPTIONS_PATH);
      expect(restored.open_version_id).toBeNull();
      const restoredVersion = restored.versions.find(
        (v: AssumptionVersion) => v.id === restored.effective_version_id,
      );
      // The API canonicalizes equivalent decimal strings ("1.0" -> "1").
      const values = (presets: AssumptionVersion["presets"]) =>
        Object.fromEntries(
          Object.entries(presets).map(([scenario, fields]) => [
            scenario,
            Object.fromEntries(
              Object.entries(fields).map(([key, value]) => [
                key,
                Number(value),
              ]),
            ),
          ]),
        );
      expect(values(restoredVersion.presets)).toEqual(
        values(approvedV1.presets),
      );
      const restoredRun = await apiSend(
        page,
        "analyst",
        "POST",
        `/banks/${SAMPLE_BANK_ID}/forecast/runs`,
        { reporting_period_id: period.id, scenario_code: "base" },
      );
      expect(restoredRun.status).toBe("succeeded");
      expect(restoredRun.input_hash).toBe(baseRun.input_hash);
      expect(restoredRun.path).toEqual(baseRun.path);
      expect(restoredRun.summary).toEqual(baseRun.summary);
      if (evidenceDir) {
        writeFileSync(
          path.join(evidenceDir, "forecasting-restored-presets.json"),
          JSON.stringify(
            { register: restored, originalRun: baseRun, restoredRun },
            null,
            2,
          ),
        );
      }
    }

    if (evidenceDir) {
      await page.screenshot({
        path: path.join(evidenceDir, "forecasting-assumptions.png"),
        fullPage: true,
      });
    }
  });
});

/** The approver opens the register in their own session and approves the pending version. */
async function approveAsApprover(browser: Browser, note: string) {
  const context = await browser.newContext({
    storageState: path.join(E2E_TMP, "approver.json"),
  });
  try {
    const checker = await context.newPage();
    await checker.goto("/forecasting/assumptions");
    const pending = section(checker, "Version 2 — pending change");
    await expect(pending).toContainText("Awaiting approval");
    await pending.getByLabel("Decision note").fill(note);
    const decided = checker.waitForResponse(
      (r) =>
        r.url().includes(`${ASSUMPTIONS_PATH}/`) &&
        r.url().endsWith("/approve") &&
        r.request().method() === "POST",
    );
    await pending.getByRole("button", { name: "Approve" }).click();
    expect((await decided).status()).toBe(200);
    await expect(
      section(checker, "Approved assumptions in force"),
    ).toContainText("Version 2");
  } finally {
    await context.close();
  }
}

/** Send one assumption-register write as `role`; the response body. */
async function apiSend(
  page: Page,
  role: "analyst" | "approver",
  method: "POST" | "PATCH",
  pathName: string,
  data: unknown,
) {
  const response = await page.request.fetch(
    `${E2E_API_ORIGIN}/api/v1${pathName}`,
    {
      method,
      data,
      headers: { Authorization: `Bearer ${await mintBackendToken(role)}` },
    },
  );
  expect(response.ok(), await response.text()).toBe(true);
  return response.json();
}

/**
 * Make `presets` the newest approved version again, through the same
 * maker-checker path: settle any version left in flight, then draft, submit
 * and approve. Values are value-based inputs, so later runs hash exactly as
 * they did under version 1.
 */
async function restoreApprovedPresets(
  page: Page,
  presets: Record<string, Record<string, string>>,
) {
  const register = await apiGet(page, "analyst", ASSUMPTIONS_PATH);
  const open = register.versions.find(
    (v: AssumptionVersion) => v.id === register.open_version_id,
  ) as AssumptionVersion | undefined;
  if (open?.status === "submitted") {
    await apiSend(
      page,
      "approver",
      "POST",
      `${ASSUMPTIONS_PATH}/${open.id}/reject`,
      {
        note: "Journey teardown",
      },
    );
  }
  const restore =
    open?.status === "draft"
      ? await apiSend(
          page,
          "analyst",
          "PATCH",
          `${ASSUMPTIONS_PATH}/${open.id}`,
          {
            presets,
          },
        )
      : await apiSend(page, "analyst", "POST", ASSUMPTIONS_PATH, {
          effective_from: register.as_of,
          presets,
          change_note: "Journey teardown: the fixture's approved presets",
        });
  await apiSend(
    page,
    "analyst",
    "POST",
    `${ASSUMPTIONS_PATH}/${restore.id}/submit`,
    {},
  );
  await apiSend(
    page,
    "approver",
    "POST",
    `${ASSUMPTIONS_PATH}/${restore.id}/approve`,
    {},
  );
}

/** `lib/api/values.ts::fmtDateUTC` for an ISO date. */
function fmtUtcDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Run a preset from the Balance Sheet header and return the saved run. */
async function runPreset(
  page: Page,
  label: string,
  code: string,
): Promise<ForecastRun> {
  await page.getByLabel("Forecast scenario").selectOption({ label });
  const run = await saveRun(page, page, "Run forecast");
  expect(run.scenario_code).toBe(code);
  return run;
}

/** Click `button` within `scope` and return the forecast run it saved. */
async function saveRun(
  page: Page,
  scope: Page | Locator,
  button: string,
): Promise<ForecastRun> {
  const saved = page.waitForResponse(
    (r) =>
      r.url().endsWith(`/banks/${SAMPLE_BANK_ID}/forecast/runs`) &&
      r.request().method() === "POST",
  );
  await scope.getByRole("button", { name: button }).click();
  const response = await saved;
  expect(response.status()).toBe(201);
  const run = (await response.json()) as ForecastRun;
  expect(run.status).toBe("succeeded");
  return run;
}

/** The run's resolved assumptions and derivable figures are the model's. */
function expectRunMatches(run: ForecastRun, a: Assumptions, p: Projection) {
  const resolved = Object.fromEntries(
    Object.entries(run.assumptions).map(([k, v]) => [k, Number(v)]),
  );
  expect(resolved).toEqual(apiAssumptions(a));
  expect(Number(run.summary.avg_roe_pct)).toBeCloseTo(p.summary.avgRoePct, 6);
  expect(Number(run.summary.year5_car_pct)).toBeCloseTo(
    p.summary.finalCarPct,
    6,
  );
  expect(Number(run.summary.cumulative_net_income)).toBeCloseTo(
    p.summary.cumulativeNetIncome,
    2,
  );
  for (const [i, year] of p.years.entries()) {
    const stored = run.path[i];
    expect(stored.year).toBe(year.year);
    expect(Number(stored.total_assets)).toBeCloseTo(year.totalAssets, 2);
    expect(Number(stored.equity)).toBeCloseTo(year.equity, 2);
    expect(Number(stored.nii)).toBeCloseTo(year.nii, 2);
    expect(Number(stored.net_income)).toBeCloseTo(year.netIncome, 2);
    expect(Number(stored.car_pct)).toBeCloseTo(year.carPct, 6);
  }
}

/** The selected run's KPIs and horizon table, read off the Balance Sheet. */
async function expectRunDashboard(page: Page, p: Projection) {
  const [y0, y1, y5] = [p.years[0], p.years[1], p.years[5]];
  await expectKpi(
    page,
    "Y1 projected asset growth",
    signedPct((y1.totalAssets / y0.totalAssets - 1) * 100),
    `${signedPct((y5.totalAssets / y0.totalAssets - 1) * 100)} over 5Y · derived from path`,
  );
  await expectKpi(
    page,
    "Year-5 CAR",
    pct(p.summary.finalCarPct),
    `BoG minimum ${CAR_MIN_PCT}%`,
  );
  await expectKpi(page, "Average ROE", pct(p.summary.avgRoePct));
  await expectKpi(
    page,
    "Cumulative net income",
    ghs(p.summary.cumulativeNetIncome),
  );

  const table = section(page, "5-year projection path").getByRole("table");
  await expect(table.locator("tbody tr")).toHaveCount(6);
  for (const [i, y] of p.years.entries()) {
    const cells = table.locator("tbody tr").nth(i).locator("td");
    const prev = p.years[i - 1];
    await expect(cells.nth(0)).toHaveText(new RegExp(`^Y${y.year}\\s`));
    await expect(cells.nth(1)).toHaveText(
      prev
        ? `${ghs(y.totalAssets)}${delta((y.totalAssets / prev.totalAssets - 1) * 100, "%", 1)}`
        : ghs(y.totalAssets),
    );
    await expect(cells.nth(2)).toHaveText(ghs(y.loans));
    await expect(cells.nth(3)).toHaveText(ghs(y.deposits));
    await expect(cells.nth(4)).toHaveText(ghs(y.equity));
    await expect(cells.nth(5)).toHaveText(
      y.year === 0
        ? "—"
        : y.year === 1
          ? ghs(y.nii)
          : `${ghs(y.nii)}${delta((y.nii / prev.nii - 1) * 100, "%", 1)}`,
    );
    await expect(cells.nth(6)).toHaveText(
      y.year === 0 ? "—" : ghs(y.netIncome),
    );
    await expect(cells.nth(7)).toHaveText(
      y.roePct === null ? "—" : pct(y.roePct),
    );
    await expect(cells.nth(8)).toHaveText(pct(y.carPct));
  }
  // Year 0 is the as-of book, so its LCR is the cockpit's.
  await expect(
    table.locator("tbody tr").first().locator("td").nth(9),
  ).toHaveText(pct(YEAR0_LCR_PCT, 1));
}

/**
 * The persisted run and the stored inputs and outputs behind it, for a
 * before/after comparison. Evidence freshness is a live annotation of the run,
 * not part of what was saved, so it is left out.
 */
async function persisted(page: Page, runId: string) {
  const stored = await apiGet(
    page,
    "analyst",
    `/banks/${SAMPLE_BANK_ID}/regulatory-runs/${runId}`,
  );
  return {
    run: await apiGet(
      page,
      "analyst",
      `/banks/${SAMPLE_BANK_ID}/forecast/runs/${runId}`,
    ),
    snapshot: {
      status: stored.status,
      input_hash: stored.input_hash,
      inputs: stored.inputs,
      metrics: stored.metrics,
      metric_results: stored.metric_results,
      validations: stored.validations,
    },
  };
}

const RESOLVED_FIELDS: [
  label: string,
  key: keyof Assumptions,
  unit: string,
  dp: number,
][] = [
  ["Loan growth", "loanGrowthPct", "%", 1],
  ["Deposit growth", "depositGrowthPct", "%", 1],
  ["Net interest margin", "nimPct", "%", 1],
  ["Fee income", "feeIncomePctAssets", "%", 1],
  ["Cost-to-income", "costToIncomePct", "%", 1],
  ["Credit loss rate", "creditLossRatePct", "%", 1],
  ["FX depreciation", "fxDepreciationPct", "%", 0],
  ["Tax rate", "taxRatePct", "%", 1],
  ["Dividend payout", "dividendPayoutPct", "%", 0],
  ["Securities shift", "securitiesShiftPp", " pp", 1],
];

/** Each resolved-assumption card shows its value and where it came from. */
async function expectResolved(
  resolved: Locator,
  a: Assumptions,
  { sourceOf }: { sourceOf: (label: string) => string },
) {
  for (const [label, key, unit, dp] of RESOLVED_FIELDS) {
    const card = resolved
      .locator("div.rounded-md")
      .filter({ has: resolved.page().getByText(label, { exact: true }) });
    await expect(card).toHaveCount(1);
    await expect(card.locator("p.font-mono")).toHaveText(
      `${a[key].toFixed(dp)}${unit}`,
    );
    await expect(card).toContainText(sourceOf(label));
  }
}

/** A preset-catalogue row: base, adverse, severely adverse, engine default. */
async function expectCatalogueRow(
  table: Locator,
  label: string,
  cells: string[],
) {
  const row = table.locator("tbody tr").filter({
    has: table.page().getByText(label, { exact: true }),
  });
  await expect(row).toHaveCount(1);
  await expect(row.locator("td").nth(0)).toContainText(label);
  for (const [i, cell] of cells.entries()) {
    await expect(row.locator("td").nth(i + 1)).toHaveText(cell);
  }
}

/** A comparison-table row keyed by its first cell: the cells after it. */
async function expectCells(table: Locator, key: string, cells: string[]) {
  const row = table.locator("tbody tr").filter({
    has: table
      .page()
      .locator("td:first-child", { hasText: new RegExp(`^${key}$`) }),
  });
  await expect(row).toHaveCount(1);
  for (const [i, cell] of cells.entries()) {
    await expect(row.locator("td").nth(i + 1)).toHaveText(cell);
  }
}

/** `components/ui/DeltaBadge` text: glyph, sign, fixed decimals, suffix. */
function delta(value: number, suffix: string, decimals: number): string {
  const glyph = value > 0 ? "▲" : value < 0 ? "▼" : "";
  return `${glyph}${value > 0 ? "+" : ""}${value.toFixed(decimals)}${suffix}`;
}
