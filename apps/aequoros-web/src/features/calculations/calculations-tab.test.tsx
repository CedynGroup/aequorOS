import type {
  CalculationRunListRead,
  CalculationRunRead,
  ForecastPeriodRead,
  ScenarioRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { CalculationsTab } from "./calculations-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};
const caseId = "90000000-0000-4000-8000-000000000001";
const scenarioId = "20000000-0000-4000-8000-000000000001";
const now = new Date("2026-07-13T12:00:00Z");

function scenario(): ScenarioRead {
  return {
    id: scenarioId,
    organizationId: tenant.orgId,
    caseId,
    name: "Baseline",
    description: "Approved plan",
    scenarioType: "baseline",
    copiedFromScenarioId: null,
    createdBy: tenant.userId,
    archivedAt: null,
    createdAt: now,
    updatedAt: now,
    assumptions: [],
  };
}

function scenarioWorkspace(
  items: ScenarioRead[] = [scenario()],
): ScenarioWorkspaceRead {
  return {
    caseId,
    scenarios: items,
    readiness: {
      caseId,
      ready: items.length > 0,
      scenarioCount: items.length,
      completeScenarioCount: items.length,
      incompleteScenarioIds: [],
    },
  };
}

function output(): ForecastPeriodRead {
  return {
    id: "40000000-0000-4000-8000-000000000001",
    periodNumber: 1,
    periodEnd: new Date("2027-06-30T00:00:00Z"),
    currency: "USD",
    totalAssets: "4550.0000",
    totalLiabilities: "1000.0000",
    totalEquity: "3550.0000",
    cash: "550.0000",
    projectedInflows: "100.0000",
    projectedOutflows: "50.0000",
    creditDraw: "0.0000",
    debtRepayment: "500.0000",
    components: {},
  };
}

function run(overrides: Partial<CalculationRunRead> = {}): CalculationRunRead {
  return {
    id: "30000000-0000-4000-8000-000000000001",
    organizationId: tenant.orgId,
    caseId,
    scenarioId,
    rerunOfRunId: null,
    status: "succeeded",
    engineVersion: "balance-sheet-v1.0.0",
    inputSchemaVersion: "calculation-input-v1",
    outputSchemaVersion: "balance-sheet-output-v1",
    inputHash: "a".repeat(64),
    inputs: {},
    forecastPeriods: 1,
    asOfDate: new Date("2026-06-30T00:00:00Z"),
    startedAt: now.toISOString(),
    completedAt: now.toISOString(),
    error: null as unknown as CalculationRunRead["error"],
    outputs: [output()],
    createdBy: tenant.userId,
    createdAt: now,
    updatedAt: now,
    ...overrides,
  };
}

function runList(items: CalculationRunRead[] = []): CalculationRunListRead {
  return {
    caseId,
    runs: items,
    latestSuccessfulRunId:
      items.find((item) => item.status === "succeeded")?.id ?? null,
  };
}

describe("CalculationsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(scenarioWorkspace());
  });

  it("shows the empty state then starts and renders a successful forecast", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "calculationRuns")
      .mockResolvedValueOnce(runList())
      .mockResolvedValue(runList([run()]));
    const start = vi
      .spyOn(riskApi, "startCalculation")
      .mockResolvedValue(run());

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    expect(await screen.findByText("No calculation runs")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Run forecast" }));

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(tenant, caseId, {
        scenarioId,
        forecastPeriods: 3,
      }),
    );
    expect(
      await screen.findByText("Projected balance sheet outputs"),
    ).toBeInTheDocument();
    expect(screen.getByText("$4,550.00")).toBeInTheDocument();
  });

  it("shows running status and disables rerun while polling", async () => {
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(
      runList([run({ status: "running", outputs: [] })]),
    );
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    expect(await screen.findByText("Forecast is running")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Rerun current inputs" }),
    ).toBeDisabled();
  });

  it("shows a failed run and links back to preserved successful output", async () => {
    const user = userEvent.setup();
    const successful = run();
    const failed = run({
      id: "30000000-0000-4000-8000-000000000002",
      status: "failed",
      outputs: [],
      error: {
        code: "scenario_not_ready",
        message: "Scenario assumptions require review.",
        details: {},
      },
      createdAt: new Date("2026-07-13T13:00:00Z"),
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue({
      caseId,
      runs: [failed, successful],
      latestSuccessfulRunId: successful.id,
    });
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    expect(await screen.findByText("scenario_not_ready")).toBeInTheDocument();
    expect(
      screen.getByText("Prior valid output preserved"),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", {
        name: "Review the latest successful forecast",
      }),
    );
    expect(await screen.findByText("$4,550.00")).toBeInTheDocument();
  });

  it("reruns current inputs and exposes query errors", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList([run()]));
    const rerun = vi
      .spyOn(riskApi, "rerunCalculation")
      .mockResolvedValue(run({ id: "new-run", rerunOfRunId: run().id }));
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", { name: "Rerun current inputs" }),
    );
    await waitFor(() =>
      expect(rerun).toHaveBeenCalledWith(tenant, caseId, run().id),
    );
  });

  it("renders loading prerequisites and request errors explicitly", async () => {
    vi.spyOn(riskApi, "calculationRuns").mockRejectedValue({
      statusCode: 503,
      code: "unavailable",
      message: "Calculation service unavailable",
    });
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    expect(
      await screen.findByText("Calculation service unavailable"),
    ).toBeInTheDocument();
  });
});
