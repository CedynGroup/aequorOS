/** Public API checks against the disposable running stack, with scoped book edits. */
import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { writeFile } from "node:fs/promises";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_TMP } from "../playwright.config";
import { SAMPLE_BANK_ID, apiGet } from "./support/figures";
import { mintBackendToken } from "./support/mint";

test("swap edits change new stress evidence, preserve recorded runs, and fail closed on unsupported direction", async ({
  page,
}) => {
  test.setTimeout(120_000);
  const headers = {
    Authorization: `Bearer ${await mintBackendToken("analyst")}`,
  };
  const root = `/banks/${SAMPLE_BANK_ID}`;
  const periods = await apiGet(page, "analyst", `${root}/reporting-periods`);
  const scenarios = await apiGet(
    page,
    "analyst",
    `/macro-scenarios?bank_id=${SAMPLE_BANK_ID}`,
  );
  const scenario = scenarios.scenarios.find(
    (s: { code: string }) => s.code === "system_irr_parallel_up_200",
  );
  expect(scenario).toBeDefined();
  const body = {
    scenario_id: scenario.id,
    reporting_period_id: periods.periods[0].id,
    reason: "IRRBB reproducibility and refusal journey",
  };
  const run = async () => {
    const response = await page.request.post(
      `${E2E_API_ORIGIN}/api/v1${root}/enterprise-stress/runs`,
      { headers, data: body },
    );
    expect(response.status(), await response.text()).toBe(201);
    return response.json();
  };
  // This edits only the isolated Playwright database, never a configured service.
  const fixture = (mode: string, runId = "") =>
    JSON.parse(
      execFileSync(
        path.join(__dirname, "../../.venv/bin/python"),
        [
          "-c",
          `
import json, sqlite3, sys
with sqlite3.connect(sys.argv[1]) as db:
    period = sys.argv[2].replace('-', '')
    mode = sys.argv[3]
    row = db.execute("SELECT id, amount, attributes FROM bank_financial_facts WHERE bank_id='BK-SAMP0001' AND reporting_period_id=? AND fact_group='irr_swap'", (period,)).fetchone()
    assert row is not None
    attrs = json.loads(row[2])
    if mode == 'double':
        db.execute("UPDATE bank_financial_facts SET amount=? WHERE id=?", (row[1]*2, row[0]))
    elif mode == 'invalid':
        attrs['direction'] = 'basis'
        db.execute("UPDATE bank_financial_facts SET attributes=? WHERE id=?", (json.dumps(attrs), row[0]))
    elif mode == 'restore':
        original = json.loads(sys.argv[5])
        db.execute("UPDATE bank_financial_facts SET amount=?, attributes=? WHERE id=?", (original[1], original[2], row[0]))
    elif mode == 'legacy':
        # A recorded v1 payload fixture: reading or rerunning may never upgrade it.
        rid = sys.argv[4].replace('-', '')
        metrics = json.loads(db.execute("SELECT metrics FROM regulatory_runs WHERE id=?", (rid,)).fetchone()[0])
        metrics['outcome']['engine_version'] = 'enterprise-stress-v1.0.0'
        metrics['outcome']['irr']['base_eve'] = '-140674640.7812'
        metrics['outcome']['irr']['delta_nii'] = '-9442600.0000'
        db.execute("UPDATE regulatory_runs SET engine_version='enterprise-stress-v1.0.0', input_schema_version='enterprise-stress-input-v2', metrics=? WHERE id=?", (json.dumps(metrics), rid))
    print(json.dumps(row))
`,
          path.join(E2E_TMP, "e2e.db"),
          body.reporting_period_id,
          mode,
          runId,
          JSON.stringify(original ?? null),
        ],
        { encoding: "utf8" },
      ),
    );
  let original: unknown = null;
  original = fixture("read");
  try {
    const first = await run();
    const firstStored = await apiGet(
      page,
      "analyst",
      `${root}/regulatory-runs/${first.run_id}`,
    );
    expect(first.engine_version).toBe("enterprise-stress-v4.2.0");
    expect(first.outcome.engine_version).toBe(first.engine_version);
    expect(firstStored.input_schema_version).toBe("enterprise-stress-input-v3");
    const regulatoryResponse = await page.request.post(
      `${E2E_API_ORIGIN}/api/v1${root}/irr/run-all-scenarios`,
      { headers, data: { reporting_period_id: body.reporting_period_id } },
    );
    expect(regulatoryResponse.status(), await regulatoryResponse.text()).toBe(
      201,
    );
    const regulatoryBatch = await regulatoryResponse.json();
    const regulatory = regulatoryBatch.runs.find(
      (run: { scenario_code: string }) => run.scenario_code === "baseline",
    );
    expect(regulatory).toBeDefined();
    const facts = firstStored.inputs.irr_facts;
    const swap = facts.find(
      (fact: { fact_group: string }) => fact.fact_group === "irr_swap",
    );
    expect(swap.attributes.receive_bucket).toBe("1-3m");
    expect(swap.attributes.pay_bucket).toBe("1-3y");
    const curve = firstStored.inputs.irr_base_curve;
    // Independent contractual carry from the snapshot, including both swap legs.
    const positionNii = facts
      .filter(
        (fact: { fact_group: string }) => fact.fact_group === "irr_position",
      )
      .reduce(
        (
          nii: number,
          fact: {
            amount: string;
            attributes: { side: string; rate_pct: string };
          },
        ) => {
          expect(Number(fact.attributes.rate_pct)).not.toBe(0);
          return (
            nii +
            ((fact.attributes.side === "asset" ? 1 : -1) *
              Number(fact.amount) *
              Number(fact.attributes.rate_pct)) /
              100
          );
        },
        0,
      );
    const floatingRate = Number(curve[swap.attributes.receive_midpoint_years]);
    const fixedRate = Number(swap.attributes.pay_rate_pct);
    expect(floatingRate).toBe(25.8);
    expect(fixedRate).toBe(25.3);
    const expectedNii =
      positionNii + (Number(swap.amount) * (floatingRate - fixedRate)) / 100;
    expect(Number(regulatory.metrics.nii_base_ghs)).toBeCloseTo(expectedNii, 2);
    expect(first.outcome.irr.base_eve).toBe(regulatory.metrics.eve_base_ghs);
    expect(first.outcome.irr.delta_nii).toBe(regulatory.metrics.ear_up_200_ghs);
    fixture("legacy", first.run_id);
    const legacy = await apiGet(
      page,
      "analyst",
      `${root}/enterprise-stress/runs/${first.run_id}`,
    );
    const recorded = await apiGet(
      page,
      "analyst",
      `${root}/regulatory-runs/${first.run_id}`,
    );
    expect(legacy.engine_version).toBe("enterprise-stress-v1.0.0");
    expect(legacy.outcome.irr.delta_nii).toBe("-9442600.0000");
    fixture("double");
    const changed = await run();
    expect(changed.run_id).not.toBe(first.run_id);
    expect(changed.input_hash).not.toBe(first.input_hash);
    expect(changed.outcome.irr.base_eve).not.toBe(first.outcome.irr.base_eve);
    expect(Number(changed.outcome.irr.delta_nii)).toBeCloseTo(-5_458_600, 2);
    expect(changed.outcome.capital).toEqual(first.outcome.capital);
    expect(changed.outcome.liquidity).toEqual(first.outcome.liquidity);
    expect(
      await apiGet(page, "analyst", `${root}/regulatory-runs/${first.run_id}`),
    ).toEqual(recorded);
    expect(
      await apiGet(
        page,
        "analyst",
        `${root}/enterprise-stress/runs/${first.run_id}`,
      ),
    ).toEqual(legacy);
    fixture("invalid");
    const before = await apiGet(
      page,
      "analyst",
      `${root}/enterprise-stress/runs?reporting_period_id=${body.reporting_period_id}`,
    );
    const refused = await page.request.post(
      `${E2E_API_ORIGIN}/api/v1${root}/enterprise-stress/runs`,
      { headers, data: body },
    );
    expect(refused.status(), await refused.text()).toBe(409);
    const refusal = await refused.json();
    expect(refusal.error.details.error_code).toBe("unsupported_swap_direction");
    expect(
      await apiGet(
        page,
        "analyst",
        `${root}/enterprise-stress/runs?reporting_period_id=${body.reporting_period_id}`,
      ),
    ).toEqual(before);
    if (process.env.E2E_EVIDENCE_DIR) {
      await writeFile(
        path.join(process.env.E2E_EVIDENCE_DIR, "irrbb-input-boundaries.json"),
        JSON.stringify(
          {
            corrected: firstStored,
            independentCarry: {
              floatingRate,
              fixedRate,
              expectedNii,
              regulatory,
            },
            recordedLegacyFixture: recorded,
            changed,
            refusal,
          },
          null,
          2,
        ),
      );
    }
  } finally {
    fixture("restore");
  }
});
