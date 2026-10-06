/**
 * The enterprise stress workbench, driven the way an analyst drives it.
 *
 * Every module's stress tab mounts the same workbench, so the functional
 * journeys share one way to run a governed scenario from it and one way to
 * prove the run they started is the one the platform kept.
 */

import { expect, type Page } from "@playwright/test";
import type { EnterpriseStressRunSummary } from "../../components/stress/types";
import { SAMPLE_BANK_ID, apiGet, section } from "./figures";

/** The slice of `EnterpriseStressRead` the journeys assert on. */
export type StressRun = {
  run_id: string;
  reporting_period_id: string;
  scenario_id: string;
  scenario_code: string;
  input_hash: string;
  summary: Record<string, string | boolean | null>;
};

/**
 * Select the approved scenario `option` (its label as the picker shows it,
 * "{name} ({severity})"), state why, run it, and return the minted run.
 */
export async function runEnterpriseStress(
  page: Page,
  option: string,
): Promise<StressRun> {
  await expect(
    page.getByRole("heading", { name: "Run enterprise stress" }),
  ).toBeVisible();
  await page.getByLabel("Approved scenario").selectOption({ label: option });
  await page
    .getByLabel("Run reason (required — governance)")
    .fill(`Journey: ${option}`);
  const minted = page.waitForResponse(
    (r) =>
      /\/enterprise-stress\/runs$/.test(r.url()) &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Run enterprise stress", exact: true })
    .click();
  const response = await minted;
  expect(response.status(), await response.text()).toBe(201);
  return (await response.json()) as StressRun;
}

/**
 * The run is the platform's latest for its date and scenario, and a fresh page
 * lists it in the run registry with the stressed CAR it was minted with.
 */
export async function expectPersistedStressRun(
  page: Page,
  run: StressRun,
  stressedCar: string,
): Promise<void> {
  const latest = await apiGet<StressRun>(
    page,
    "analyst",
    `/banks/${SAMPLE_BANK_ID}/enterprise-stress/latest?reporting_period_id=${run.reporting_period_id}&scenario_id=${run.scenario_id}`,
  );
  expect(latest.run_id).toBe(run.run_id);
  expect(latest.input_hash).toBe(run.input_hash);

  await page.reload();
  const registry = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname ===
        `/api/v1/banks/${SAMPLE_BANK_ID}/enterprise-stress/runs` &&
      url.searchParams.get("reporting_period_id") === run.reporting_period_id &&
      response.request().method() === "GET"
    );
  });
  await page.getByRole("button", { name: "Run registry", exact: true }).click();
  const response = await registry;
  expect(response.status(), await response.text()).toBe(200);
  const runs = (await response.json()) as EnterpriseStressRunSummary[];
  const newest = runs.find(
    (entry) => entry.scenario_code === run.scenario_code,
  );
  expect(newest?.run_id).toBe(run.run_id);
  expect(newest?.input_hash).toBe(run.input_hash);
  const row = section(page, "Run registry")
    .locator("tbody tr")
    .filter({ hasText: run.scenario_code })
    .first();
  await expect(row).toContainText(`hash ${run.input_hash.slice(0, 10)}`);
  await expect(row.locator("td").nth(1)).toHaveText(stressedCar);
}
