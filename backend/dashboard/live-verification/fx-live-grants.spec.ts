import { expect, test } from "@playwright/test";
import { writeFileSync } from "node:fs";
import path from "node:path";
import { E2E_API_ORIGIN, E2E_BASE_URL } from "../playwright.config";
import {
  E2E_USERS,
  mintBackendToken,
  mintSessionCookie,
} from "../e2e/support/mint";

test("real FX grants enforce sensitivity across API, Reports and workbench", async ({
  page,
}) => {
  test.setTimeout(180_000);
  const api = `${E2E_API_ORIGIN}/api/v1`;
  const bank = "/banks/BK-SAMP0001";
  const evidence: unknown[] = [];
  let version = 1;
  const owner = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  async function request(
    method: string,
    route: string,
    status: number,
    data?: unknown,
  ) {
    const response = await page.request.fetch(`${api}${route}`, {
      method,
      data,
      headers: {
        Authorization: `Bearer ${await mintBackendToken("grant_member", version)}`,
      },
    });
    const body = await response.json();
    evidence.push({ method, route, status: response.status(), body });
    expect(response.status(), JSON.stringify(body)).toBe(status);
    return body;
  }
  async function grant(
    module_scope: string,
    sensitivity_scope: string,
    role_bundle = "viewer",
  ) {
    const data = {
      principal_user_id: E2E_USERS.grant_member.id,
      role_bundle,
      institution_scope: "institution",
      institution_id: "BK-SAMP0001",
      module_scope,
      sensitivity_scope,
      reason: "Live FX authorization verification",
    };
    const preview = await page.request.post(
      `${api}/authorization/bindings/preview`,
      { headers: owner, data },
    );
    expect(preview.status()).toBe(200);
    const created = await page.request.post(`${api}/authorization/bindings`, {
      headers: owner,
      data: {
        ...data,
        expected_authority_sentence: (await preview.json()).authority_sentence,
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    version += 1;
  }
  async function signIn() {
    await page.context().clearCookies();
    await page.context().addCookies([
      {
        name: "authjs.session-token",
        value: await mintSessionCookie("grant_member", version),
        url: E2E_BASE_URL,
        httpOnly: true,
        sameSite: "Lax",
      },
    ]);
    await page.addInitScript(() => localStorage.setItem("aeq-tour-done", "1"));
  }
  async function screenshot(name: string) {
    if (process.env.E2E_EVIDENCE_DIR)
      await page.screenshot({
        path: path.join(process.env.E2E_EVIDENCE_DIR, name),
        fullPage: true,
      });
  }
  await request("GET", `${bank}/fx/dashboard`, 403);
  await grant("reg", "all");
  await grant("fx", "confidential");
  await request("GET", `${bank}/fx/dashboard`, 403);
  await request("GET", `${bank}/scenario-workbench/fx/scenarios`, 200);
  await request("GET", `${bank}/scenario-workbench/fx/analyses`, 403);
  await request("POST", `${bank}/scenario-workbench/fx/scenarios`, 403, {
    code: "live_fx",
    name: "Live FX",
    shocks: { ghs_usd_shock_pct: "12.5" },
  });
  const before = await request("GET", `${bank}/live-summary`, 200);
  expect(
    before.modules.every((row: { module: string }) => row.module !== "fx"),
  ).toBeTruthy();
  await request("GET", `${bank}/live-snapshots?module=fx`, 403);
  await request("POST", `${bank}/official-runs`, 403, {
    as_of_date: "2026-08-31",
    reason: "Denied viewer execution",
  });
  await signIn();
  await page.goto("/reports");
  await expect(
    page.getByRole("button", { name: "All modules", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "FX", exact: true }),
  ).toHaveCount(0);
  await screenshot("fx-live-confidential-reports.png");
  await page.goto("/fx/scenarios");
  await page
    .getByRole("button", { name: "Scenarios & run", exact: true })
    .click();
  const run = page.getByRole("button", { name: "Run enterprise stress" });
  await expect(run).toBeDisabled();
  await run.locator("..").hover();
  await expect(page.getByRole("tooltip")).toContainText(
    "Requires FX run permission at confidential sensitivity",
  );
  await screenshot("fx-live-run-disabled.png");
  await grant("fx", "aggregated");
  await request("GET", `${bank}/fx/dashboard`, 200);
  await request("GET", `${bank}/scenario-workbench/fx/analyses`, 200);
  const after = await request("GET", `${bank}/live-summary`, 200);
  expect(
    after.modules.some((row: { module: string }) => row.module === "fx"),
  ).toBeTruthy();
  await request("GET", `${bank}/live-snapshots?module=fx`, 200);
  await signIn();
  await page.goto("/reports");
  await expect(
    page.getByRole("button", { name: "FX", exact: true }),
  ).toBeVisible();
  await screenshot("fx-live-aggregated-reports.png");
  await grant("fx", "confidential", "analyst");
  const created = await request(
    "POST",
    `${bank}/scenario-workbench/fx/scenarios`,
    201,
    { code: "live_fx", name: "Live FX", shocks: { ghs_usd_shock_pct: "12.5" } },
  );
  await request(
    "PATCH",
    `${bank}/scenario-workbench/fx/scenarios/${created.id}`,
    200,
    { name: "Edited live FX" },
  );
  if (process.env.E2E_EVIDENCE_DIR)
    writeFileSync(
      path.join(process.env.E2E_EVIDENCE_DIR, "fx-live-api.json"),
      JSON.stringify(evidence, null, 2),
    );
});

test("enterprise readers retain non-FX results and null-contribution totals", async ({
  request,
}) => {
  const api = `${E2E_API_ORIGIN}/api/v1`;
  const bank = "/banks/BK-SAMP0001";
  const owner = { Authorization: `Bearer ${await mintBackendToken("admin")}` };
  const reader = {
    Authorization: `Bearer ${await mintBackendToken("liquidity_viewer")}`,
  };
  const evidence: unknown[] = [];
  const levels = {
    gdp_growth: ["0.05", "0.00"],
    interest_rate: ["0.20", "0.25"],
    inflation: ["0.15", "0.21"],
    unemployment: ["0.06", "0.09"],
    fx_usd_ghs: ["12.5", "15.0"],
    gse_index: ["5000", "3500"],
    gog_yield: ["0.22", "0.26"],
  };
  const scenarioResponse = await request.post(`${api}/macro-scenarios`, {
    headers: owner,
    data: {
      code: "live_projection",
      name: "Live projection",
      scenario_type: "adverse",
      severity: "severe",
      horizon_years: 3,
      narrative: "Live FX field projection",
      source: "Disposable test",
      reason: "Exercise projection",
      paths: Object.entries(levels).flatMap(([variable, values]) =>
        [1, 2, 3].map((year_index) => ({
          variable,
          year_index,
          base_value: values[0],
          stress_value: values[1],
        })),
      ),
    },
  });
  expect(scenarioResponse.status(), await scenarioResponse.text()).toBe(201);
  const scenario = await scenarioResponse.json();
  expect(
    (
      await request.post(`${api}/macro-scenarios/${scenario.id}/submit`, {
        headers: owner,
        data: { reason: "Ready" },
      })
    ).status(),
  ).toBe(200);
  expect(
    (
      await request.post(`${api}/macro-scenarios/${scenario.id}/approve`, {
        headers: {
          Authorization: `Bearer ${await mintBackendToken("approver")}`,
        },
        data: { reason: "Reviewed" },
      })
    ).status(),
  ).toBe(200);
  const periods = await (
    await request.get(`${api}${bank}/reporting-periods`, { headers: owner })
  ).json();
  const period = periods.periods[0].id;
  for (const include_fx of [true, false]) {
    const created = await request.post(`${api}${bank}/enterprise-stress/runs`, {
      headers: owner,
      data: {
        scenario_id: scenario.id,
        reporting_period_id: period,
        include_fx,
        reason: "Live field filtering",
      },
    });
    expect(created.status(), await created.text()).toBe(201);
    const original = await created.json();
    const routes = [
      `${bank}/regulatory-runs?module=enterprise_stress&limit=100`,
      `${bank}/regulatory-runs/${original.run_id}`,
      `${bank}/enterprise-stress/latest?reporting_period_id=${period}&scenario_id=${scenario.id}`,
      `${bank}/enterprise-stress/runs/${original.run_id}`,
    ];
    for (const route of routes) {
      const response = await request.get(`${api}${route}`, { headers: reader });
      expect(response.status(), await response.text()).toBe(200);
      const body = await response.json();
      evidence.push({ include_fx, route, status: response.status(), body });
      const metrics = body.runs
        ? body.runs.find((r: { id: string }) => r.id === original.run_id)
            ?.metrics
        : (body.metrics ?? body);
      expect(metrics).toBeTruthy();
      expect(metrics.outcome.fx).toBeUndefined();
      expect(metrics.outcome.capital).toEqual(original.outcome.capital);
      expect(metrics.outcome.liquidity).toEqual(original.outcome.liquidity);
      expect(metrics.appendix_ii.table6_risk_drivers.rows).toEqual(
        original.appendix_ii.table6_risk_drivers.rows.filter(
          (r: { variable: string }) => r.variable !== "fx_usd_ghs",
        ),
      );
      for (let i = 0; i < original.appendix_ii.table5_rwa.rows.length; i++) {
        const before = original.appendix_ii.table5_rwa.rows[i];
        const after = metrics.appendix_ii.table5_rwa.rows[i];
        expect(after.pillar2.country_and_fx).toBeUndefined();
        if (before.pillar2.country_and_fx != null) {
          expect(after.pillar2.total).toBeUndefined();
          expect(after.total_capital_requirement).toBeUndefined();
        } else {
          expect(after.pillar2.total).toEqual(before.pillar2.total);
          expect(after.total_capital_requirement).toEqual(
            before.total_capital_requirement,
          );
        }
      }
    }
  }
  if (process.env.E2E_EVIDENCE_DIR)
    writeFileSync(
      path.join(process.env.E2E_EVIDENCE_DIR, "fx-live-enterprise.json"),
      JSON.stringify(evidence, null, 2),
    );
});
