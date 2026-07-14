import { expect, test, type Page, type Route } from "playwright/test";

import { RiskConsolePage } from "./support/risk-console-page";
import { demoTenant, northstarCase } from "./support/test-data";

const now = "2026-07-13T12:00:00Z";
const runId = "30000000-0000-4000-8000-000000000001";
const projectionId = "40000000-0000-4000-8000-000000000001";
const scenarioId = "20000000-0000-4000-8000-000000000001";

async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function queueCase() {
  return {
    id: northstarCase.id,
    organization_id: demoTenant.orgId,
    title: northstarCase.title,
    case_type: "financial_statement_review",
    subject_name: "Northstar Foods",
    subject_type: "company",
    status: "open",
    risk_level: "medium",
    risk_score: 55,
    decision: null,
    assigned_to_user_id: demoTenant.userId,
    assignee_display_name: "Reviewer",
    assignee_email: "reviewer@example.com",
    findings_count: 1,
    open_findings_count: 1,
    created_at: now,
    updated_at: now,
  };
}

function projection() {
  return {
    id: projectionId,
    organization_id: demoTenant.orgId,
    case_id: northstarCase.id,
    scenario_id: scenarioId,
    calculation_run_id: runId,
    status: "succeeded",
    engine_version: "capital-projection-v1.0.0",
    input_hash: "a".repeat(64),
    started_at: now,
    completed_at: now,
    error: null,
    indicators: [
      {
        id: "50000000-0000-4000-8000-000000000001",
        forecast_period_id: "60000000-0000-4000-8000-000000000001",
        period_number: 1,
        equity: "50.0000",
        equity_to_assets_ratio: "0.05263158",
        liabilities_to_assets_ratio: "0.94736842",
        equity_change: "-100.0000",
        pressure_level: "high",
        evidence: { calculation_run_id: runId },
      },
    ],
    findings: [
      {
        finding: {
          id: "70000000-0000-4000-8000-000000000001",
          organization_id: demoTenant.orgId,
          case_id: northstarCase.id,
          assessment_id: null,
          run_id: null,
          risk_type: "leverage_risk",
          title: "Projected capital buffer is thin",
          summary: "The minimum equity-to-assets ratio is 5.26% in period 1.",
          rationale: "Deterministic capital projection rule.",
          severity: "high",
          likelihood: null,
          impact: null,
          confidence: null,
          status: "needs_review",
          disposition_reason: null,
          source: "deterministic_rule",
          rule_id: "capital_thin_buffer",
          rule_version: "capital-projection-v1.0.0",
          score_impact: null,
          details: { calculation_run_id: runId },
          created_at: now,
          updated_at: now,
        },
        evidence: [
          {
            id: "80000000-0000-4000-8000-000000000001",
            finding_id: "70000000-0000-4000-8000-000000000001",
            document_id: null,
            document_chunk_id: null,
            page_number: null,
            quote: "The minimum equity-to-assets ratio is 5.26% in period 1.",
            locator: {
              source_type: "calculation_forecast_period",
              calculation_run_id: runId,
            },
            relevance: "1",
            created_at: now,
            document: null,
            chunk: null,
          },
        ],
      },
    ],
    created_by: demoTenant.userId,
    created_at: now,
    updated_at: now,
  };
}

async function installBackend(
  page: Page,
  failSummary = false,
  incompatibleComparison = false,
) {
  let generated = false;
  await page.route("http://127.0.0.1:8003/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const method = request.method();
    const orgId = request.headers()["x-org-id"];
    if (orgId !== demoTenant.orgId) {
      return json(
        route,
        {
          error: { code: "not_found", message: "Case not found.", details: {} },
        },
        404,
      );
    }
    if (path === "/api/v1/taxonomies/cases") {
      return json(route, {
        statuses: ["open"],
        risk_levels: ["medium"],
        decisions: [],
        sort_options: ["updated_at_desc"],
      });
    }
    if (path === "/api/v1/cases" && method === "GET") {
      return json(route, {
        items: [queueCase()],
        total: 1,
        limit: 12,
        offset: 0,
        page: 1,
        pages: 1,
        has_more: false,
      });
    }
    if (path === `/api/v1/cases/${northstarCase.id}`) {
      return json(route, {
        ...queueCase(),
        description: "Capital review case",
        metadata: {},
        created_by: demoTenant.userId,
        assigned_at: now,
        decided_at: null,
        scored_at: now,
        scoring_version: "v1",
        archived_at: null,
      });
    }
    if (path.endsWith("/scenarios") && method === "GET") {
      return json(route, {
        case_id: northstarCase.id,
        scenarios: [
          {
            id: scenarioId,
            organization_id: demoTenant.orgId,
            case_id: northstarCase.id,
            name: "Operating baseline",
            description: null,
            scenario_type: "baseline",
            copied_from_scenario_id: null,
            created_by: demoTenant.userId,
            archived_at: null,
            created_at: now,
            updated_at: now,
            assumptions: [],
          },
        ],
        readiness: {
          case_id: northstarCase.id,
          ready: true,
          scenario_count: 1,
          complete_scenario_count: 1,
          incomplete_scenario_ids: [],
        },
      });
    }
    if (path.endsWith("/calculation-runs") && method === "GET") {
      return json(route, {
        case_id: northstarCase.id,
        runs: [
          {
            id: runId,
            scenario_id: scenarioId,
            rerun_of_run_id: null,
            status: "succeeded",
            engine_version: "balance-sheet-v1.0.0",
            input_hash: "a".repeat(64),
            forecast_periods: 1,
            as_of_date: "2026-06-30",
            started_at: now,
            completed_at: now,
            error: null,
            created_at: now,
          },
        ],
        latest_successful_run_id: runId,
        total: 1,
        limit: 100,
        offset: 0,
        has_more: false,
      });
    }
    if (path.endsWith("/capital-summary")) {
      if (failSummary) {
        return json(
          route,
          {
            error: {
              code: "capital_unavailable",
              message: "Capital service unavailable.",
              details: {},
            },
          },
          503,
        );
      }
      return json(route, {
        case_id: northstarCase.id,
        scenario_id: generated ? scenarioId : null,
        projection: generated ? projection() : null,
      });
    }
    if (path.endsWith("/capital-comparison")) {
      return json(route, {
        case_id: northstarCase.id,
        baseline: generated ? projection() : null,
        downside: generated ? projection() : null,
        diagnostic:
          generated && incompatibleComparison
            ? {
                code: "comparison_basis_mismatch",
                message:
                  "Baseline and downside projections use incompatible forecast bases.",
                differing_attributes: [
                  "as_of_date",
                  "reporting_currency",
                  "forecast_horizon",
                ],
                baseline_basis: {
                  as_of_date: "2026-06-30",
                  reporting_currency: "USD",
                  forecast_horizon: 2,
                },
                downside_basis: {
                  as_of_date: "2026-07-01",
                  reporting_currency: "EUR",
                  forecast_horizon: 3,
                },
                corrective_action:
                  "Rerun the other scenario using the matching as-of date, reporting currency, and forecast horizon, then generate a new capital projection.",
              }
            : null,
        periods:
          generated && !incompatibleComparison
            ? [
                {
                  period_number: 1,
                  baseline_equity: "75.0000",
                  downside_equity: "50.0000",
                  equity_delta: "-25.0000",
                  baseline_equity_to_assets_ratio: "0.07500000",
                  downside_equity_to_assets_ratio: "0.05263158",
                  equity_to_assets_ratio_delta: "-0.02236842",
                },
              ]
            : [],
      });
    }
    if (path.endsWith("/capital-projections") && method === "GET") {
      return json(route, {
        case_id: northstarCase.id,
        projections: generated
          ? [
              {
                id: projectionId,
                scenario_id: scenarioId,
                calculation_run_id: runId,
                status: "succeeded",
                started_at: now,
                completed_at: now,
                created_at: now,
              },
            ]
          : [],
        total: generated ? 1 : 0,
        limit: 25,
        offset: 0,
        has_more: false,
      });
    }
    if (path.endsWith("/capital-projections") && method === "POST") {
      generated = true;
      return json(route, projection(), 201);
    }
    return json(
      route,
      {
        error: {
          code: "not_found",
          message: `Unhandled ${method} ${path}`,
          details: {},
        },
      },
      404,
    );
  });
}

test("projects capital, compares scenarios, reviews evidence, and enforces tenant isolation", async ({
  page,
}) => {
  const console = new RiskConsolePage(page);
  await console.seedTenantStorage();
  await installBackend(page);
  await console.gotoSelectedCase("capital");

  await expect(page.getByText("No capital projection")).toBeVisible();
  await page.getByRole("button", { name: "Generate projection" }).click();
  await expect(page.getByText("Projected capital indicators")).toBeVisible();
  await expect(
    page.getByText("Projected capital buffer is thin"),
  ).toBeVisible();
  await expect(page.getByText("Evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("Downside delta")).toBeVisible();
  await expect(page.getByText("-25.00")).toBeVisible();

  await page
    .getByLabel("Tenant org id")
    .fill("22222222-2222-4222-8222-222222222222");
  await page.getByLabel("User id").fill("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb");
  await expect(page.getByText("Case not found.").first()).toBeVisible();
});

test("shows capital API errors", async ({ page }) => {
  const console = new RiskConsolePage(page);
  await console.seedTenantStorage();
  await installBackend(page, true);
  await console.gotoSelectedCase("capital");
  await expect(page.getByText("Capital service unavailable.")).toBeVisible();
});

test("rejects incompatible capital comparison bases", async ({ page }) => {
  const console = new RiskConsolePage(page);
  await console.seedTenantStorage();
  await installBackend(page, false, true);
  await console.gotoSelectedCase("capital");

  await page.getByRole("button", { name: "Generate projection" }).click();
  await expect(
    page.getByText(
      "Baseline and downside projections use incompatible forecast bases.",
    ),
  ).toBeVisible();
  await expect(page.getByText("2026-06-30")).toBeVisible();
  await expect(page.getByText("2026-07-01")).toBeVisible();
  await expect(page.getByText("USD")).toBeVisible();
  await expect(page.getByText("EUR")).toBeVisible();
  await expect(page.getByText("2 periods")).toBeVisible();
  await expect(page.getByText("3 periods")).toBeVisible();
  await expect(page.getByText(/Rerun the other scenario/)).toBeVisible();
  await expect(page.getByText("Downside delta")).toHaveCount(0);
});
