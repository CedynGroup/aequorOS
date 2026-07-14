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
import { mockCaseHealth } from "../demo-data/demo-data";
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
    startedAt: now,
    completedAt: now,
    error: null,
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
    latestSuccessfulRunsByScenario: [],
    total: items.length,
    limit: 25,
    offset: 0,
    hasMore: false,
  };
}

describe("CalculationsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    window.history.replaceState(null, "", window.location.pathname);
    Object.defineProperty(Element.prototype, "scrollIntoView", {
      configurable: true,
      value: vi.fn<() => void>(),
    });
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(scenarioWorkspace());
    vi.spyOn(riskApi, "calculationRun").mockResolvedValue(run());
  });

  it("opens and focuses a forecast period from an evidence deep link", async () => {
    const target = `calculation-run-${run().id}-forecast-period-1`;
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());

    const { queryClient } = renderWithQuery(
      <CalculationsTab tenant={tenant} caseId={caseId} />,
    );

    await waitFor(() => expect(document.activeElement?.id).toBe(target));
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
    expect(riskApi.calculationRun).toHaveBeenCalledWith(
      tenant,
      caseId,
      run().id,
    );

    queryClient.setQueryData(["calculation-run", tenant, caseId, run().id], {
      ...run(),
      completedAt: new Date("2026-07-13T13:00:00Z"),
    });

    await waitFor(() =>
      expect(screen.getByText("$4,550.00")).toBeInTheDocument(),
    );
    expect(Element.prototype.scrollIntoView).toHaveBeenCalledTimes(1);
  });

  it("preserves an archived evidence run in read-only audit mode", async () => {
    const target = `calculation-run-${run().id}-forecast-period-1`;
    const archivedScenario = { ...scenario(), archivedAt: now };
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    vi.mocked(riskApi.scenarios).mockResolvedValue(
      scenarioWorkspace([archivedScenario]),
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("Archived forecast audit"),
    ).toBeInTheDocument();
    expect(screen.getByText("Archived", { exact: true })).toBeInTheDocument();
    expect(
      screen.getByText("Archived scenario · read only"),
    ).toBeInTheDocument();
    expect(screen.getByText("$4,550.00")).toBeInTheDocument();
    expect(riskApi.scenarios).toHaveBeenCalledWith(tenant, caseId, true);
    expect(
      screen.queryByRole("button", { name: "Run forecast" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Rerun current inputs" }),
    ).not.toBeInTheDocument();
  });

  it("makes archived runs selected from history read-only", async () => {
    const user = userEvent.setup();
    const activeRun = run();
    const archivedScenarioId = "20000000-0000-4000-8000-000000000002";
    const archivedRun = run({
      id: "30000000-0000-4000-8000-000000000002",
      scenarioId: archivedScenarioId,
      createdAt: new Date("2026-07-12T12:00:00Z"),
    });
    vi.mocked(riskApi.scenarios).mockResolvedValue(
      scenarioWorkspace([
        scenario(),
        {
          ...scenario(),
          id: archivedScenarioId,
          name: "Archived downside",
          archivedAt: now,
        },
      ]),
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(
      runList([activeRun, archivedRun]),
    );
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        Promise.resolve(
          requestedRunId === archivedRun.id ? archivedRun : activeRun,
        ),
    );

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", { name: /Archived downside/ }),
    );

    expect(
      await screen.findByText("Archived forecast audit"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Archived scenario · read only"),
    ).toBeInTheDocument();
    expect(riskApi.scenarios).toHaveBeenCalledWith(tenant, caseId, true);
    expect(screen.getByRole("button", { name: "Run forecast" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Rerun current inputs" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    ["demo", "Mutation unavailable in demo mode", "Demo mode · read only"],
    [
      "retired-case",
      "Forecast mutations unavailable for retired case",
      "Retired case · read only",
    ],
  ] as const)(
    "disables every forecast mutation for %s workspaces",
    async (mutationDisabledReason, panelTitle, runTitle) => {
      const user = userEvent.setup();
      vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList([run()]));
      const start = vi.spyOn(riskApi, "startCalculation");
      const rerun = vi.spyOn(riskApi, "rerunCalculation");

      renderWithQuery(
        <CalculationsTab
          tenant={tenant}
          caseId={caseId}
          mutationDisabled
          mutationDisabledReason={mutationDisabledReason}
        />,
      );

      expect(await screen.findByText(panelTitle)).toBeInTheDocument();
      expect(await screen.findByText(runTitle)).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Run forecast" }),
      ).toBeDisabled();
      expect(screen.getByLabelText("Forecast scenario")).toBeDisabled();
      expect(screen.getByLabelText("Forecast periods")).toBeDisabled();
      expect(
        screen.queryByRole("button", { name: "Rerun current inputs" }),
      ).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Run forecast" }));
      expect(start).not.toHaveBeenCalled();
      expect(rerun).not.toHaveBeenCalled();
    },
  );

  it("renders complete demo forecasts without live queries or mutations", async () => {
    const calculationRuns = vi.spyOn(riskApi, "calculationRuns");
    const start = vi.spyOn(riskApi, "startCalculation");
    const rerun = vi.spyOn(riskApi, "rerunCalculation");
    const demoData = mockCaseHealth(tenant.orgId, caseId);

    renderWithQuery(
      <CalculationsTab
        tenant={tenant}
        caseId={caseId}
        mutationDisabled
        mutationDisabledReason="demo"
        demoData={demoData}
      />,
    );

    expect(
      await screen.findByText("Projected balance sheet outputs"),
    ).toBeInTheDocument();
    expect(screen.getByText("Demo mode · read only")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run forecast" })).toBeDisabled();
    expect(riskApi.scenarios).not.toHaveBeenCalled();
    expect(calculationRuns).not.toHaveBeenCalled();
    expect(riskApi.calculationRun).not.toHaveBeenCalled();
    expect(start).not.toHaveBeenCalled();
    expect(rerun).not.toHaveBeenCalled();
  });

  it.each([
    "calculation-run-not-a-uuid-forecast-period-1",
    `calculation-run-${run().id}-forecast-period-0`,
    `calculation-run-${run().id}-forecast-period-1.5`,
    `calculation-run-${run().id}-forecast-period-invalid`,
  ])("ignores malformed calculation evidence fragment %s", async (target) => {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByText("No calculation runs")).toBeInTheDocument();
    expect(riskApi.calculationRun).not.toHaveBeenCalled();
  });

  it("preserves an archived evidence run in read-only audit mode", async () => {
    const target = `calculation-run-${run().id}-forecast-period-1`;
    const archivedScenario = { ...scenario(), archivedAt: now };
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    vi.mocked(riskApi.scenarios).mockResolvedValue(
      scenarioWorkspace([archivedScenario]),
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("Archived forecast audit"),
    ).toBeInTheDocument();
    expect(screen.getByText("Archived", { exact: true })).toBeInTheDocument();
    expect(
      screen.getByText("Archived scenario · read only"),
    ).toBeInTheDocument();
    expect(screen.getByText("$4,550.00")).toBeInTheDocument();
    expect(riskApi.scenarios).toHaveBeenCalledWith(tenant, caseId, true);
    expect(
      screen.queryByRole("button", { name: "Run forecast" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Rerun current inputs" }),
    ).not.toBeInTheDocument();
  });

  it("makes archived runs selected from history read-only", async () => {
    const user = userEvent.setup();
    const activeRun = run();
    const archivedScenarioId = "20000000-0000-4000-8000-000000000002";
    const archivedRun = run({
      id: "30000000-0000-4000-8000-000000000002",
      scenarioId: archivedScenarioId,
      createdAt: new Date("2026-07-12T12:00:00Z"),
    });
    vi.mocked(riskApi.scenarios).mockResolvedValue(
      scenarioWorkspace([
        scenario(),
        {
          ...scenario(),
          id: archivedScenarioId,
          name: "Archived downside",
          archivedAt: now,
        },
      ]),
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(
      runList([activeRun, archivedRun]),
    );
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        Promise.resolve(
          requestedRunId === archivedRun.id ? archivedRun : activeRun,
        ),
    );

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", { name: /Archived downside/ }),
    );

    expect(
      await screen.findByText("Archived forecast audit"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Archived scenario · read only"),
    ).toBeInTheDocument();
    expect(riskApi.scenarios).toHaveBeenCalledWith(tenant, caseId, true);
    expect(screen.getByRole("button", { name: "Run forecast" })).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Rerun current inputs" }),
    ).not.toBeInTheDocument();
  });

  it.each([
    "calculation-run-not-a-uuid-forecast-period-1",
    `calculation-run-${run().id}-forecast-period-0`,
    `calculation-run-${run().id}-forecast-period-1.5`,
    `calculation-run-${run().id}-forecast-period-invalid`,
  ])("ignores malformed calculation evidence fragment %s", async (target) => {
    window.history.replaceState(
      null,
      "",
      `${window.location.pathname}#${target}`,
    );
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByText("No calculation runs")).toBeInTheDocument();
    expect(riskApi.calculationRun).not.toHaveBeenCalled();
  });

  it("shows the empty state then starts and renders a successful forecast", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "calculationRuns")
      .mockResolvedValueOnce(runList())
      .mockResolvedValue(runList([run()]));
    const start = vi
      .spyOn(riskApi, "startCalculation")
      .mockResolvedValue(run());

    const { queryClient } = renderWithQuery(
      <CalculationsTab tenant={tenant} caseId={caseId} />,
    );
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
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
    const history = screen.getByText("Run history").closest("section");
    expect(history).toHaveClass("min-w-0", "overflow-hidden");
    expect(history?.querySelector("button")).toHaveClass("overflow-hidden");
    expect(
      screen.getByRole("table", {
        name: "Projected balance sheet outputs",
      }).parentElement,
    ).toHaveClass("max-w-full", "overflow-x-auto");
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["findings", tenant, caseId],
    });
  });

  it("shows running status and disables rerun while polling", async () => {
    const running = run({ status: "running", outputs: [] });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList([running]));
    vi.mocked(riskApi.calculationRun).mockResolvedValue(running);
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
        code: "active_obligation_amounts_missing",
        message:
          "Active obligations require principal and outstanding amounts.",
        details: {
          corrective_action: "Enter every missing amount.",
          obligations: [
            {
              id: "50000000-0000-4000-8000-000000000001",
              obligation_type: "term_loan",
              missing_fields: ["outstanding_amount"],
            },
          ],
        },
      },
      createdAt: new Date("2026-07-13T13:00:00Z"),
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(
      runList([failed, successful]),
    );
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        Promise.resolve(requestedRunId === failed.id ? failed : successful),
    );
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    expect(
      await screen.findByText("active_obligation_amounts_missing"),
    ).toBeInTheDocument();
    expect(screen.getByText("Enter every missing amount.")).toBeInTheDocument();
    expect(screen.getByText(/term_loan/)).toHaveTextContent(
      "missing outstanding_amount",
    );
    expect(
      screen.queryByText(/50000000-0000-4000-8000-000000000001/),
    ).not.toBeInTheDocument();
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

  it("shows cash-flow dates and period bounds in failure diagnostics", async () => {
    const failed = run({
      status: "failed",
      outputs: [],
      error: {
        code: "cash_flow_date_outside_reporting_period",
        message:
          "Cash-flow dates must fall within their linked reporting period.",
        details: {
          corrective_action:
            "Correct each listed record in the review workspace.",
          cash_flows: [
            {
              id: "60000000-0000-4000-8000-000000000001",
              category: "operations",
              cash_flow_date: "2027-01-01",
              period_start_date: "2026-01-01",
              period_end_date: "2026-12-31",
            },
          ],
        },
      },
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList([failed]));
    vi.mocked(riskApi.calculationRun).mockResolvedValue(failed);

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("cash_flow_date_outside_reporting_period"),
    ).toBeInTheDocument();
    expect(screen.getByText(/operations/)).toHaveTextContent(
      "cash-flow date 2027-01-01 — period 2026-01-01 to 2026-12-31",
    );
    expect(
      screen.queryByText(/60000000-0000-4000-8000-000000000001/),
    ).not.toBeInTheDocument();
    expect(screen.getByText(/review workspace/)).toBeInTheDocument();
  });

  it("fetches the latest successful run outside the current history page", async () => {
    const user = userEvent.setup();
    const successful = run();
    const failed = run({
      id: "30000000-0000-4000-8000-000000000002",
      status: "failed",
      outputs: [],
      error: {
        code: "invalid_assumption",
        message: "A reviewed assumption is invalid.",
        details: { corrective_action: "Correct and review the assumption." },
      },
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue({
      ...runList([failed]),
      latestSuccessfulRunId: successful.id,
      total: 26,
      hasMore: true,
    });
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        Promise.resolve(requestedRunId === failed.id ? failed : successful),
    );

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", {
        name: "Review the latest successful forecast",
      }),
    );

    expect(await screen.findByText("$4,550.00")).toBeInTheDocument();
    expect(riskApi.calculationRun).toHaveBeenCalledWith(
      tenant,
      caseId,
      successful.id,
    );
  });

  it("keeps history visible while an off-page run is loading", async () => {
    const user = userEvent.setup();
    const successful = run();
    const failed = run({
      id: "30000000-0000-4000-8000-000000000002",
      status: "failed",
      outputs: [],
      error: { code: "failed", message: "Forecast failed", details: null },
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue({
      ...runList([failed]),
      latestSuccessfulRunId: successful.id,
      total: 26,
      hasMore: true,
    });
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        requestedRunId === failed.id
          ? Promise.resolve(failed)
          : new Promise<CalculationRunRead>(() => undefined),
    );

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", {
        name: "Review the latest successful forecast",
      }),
    );

    expect(screen.getByText("Run history")).toBeInTheDocument();
    expect(screen.queryByText("No calculation runs")).not.toBeInTheDocument();
  });

  it("shows an off-page run retrieval error without hiding history", async () => {
    const user = userEvent.setup();
    const failed = run({
      id: "30000000-0000-4000-8000-000000000002",
      status: "failed",
      outputs: [],
      error: { code: "failed", message: "Forecast failed", details: null },
    });
    const successfulRunId = "30000000-0000-4000-8000-000000000003";
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue({
      ...runList([failed]),
      latestSuccessfulRunId: successfulRunId,
      total: 26,
      hasMore: true,
    });
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        requestedRunId === failed.id
          ? Promise.resolve(failed)
          : Promise.reject({
              statusCode: 503,
              code: "unavailable",
              message: "Run details unavailable",
            }),
    );

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", {
        name: "Review the latest successful forecast",
      }),
    );

    expect(
      await screen.findByText("Run details unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByText("Run history")).toBeInTheDocument();
    expect(screen.queryByText("No calculation runs")).not.toBeInTheDocument();
  });

  it("reruns current inputs and exposes query errors", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList([run()]));
    const rerun = vi
      .spyOn(riskApi, "rerunCalculation")
      .mockResolvedValue(run({ id: "new-run", rerunOfRunId: run().id }));
    const { queryClient } = renderWithQuery(
      <CalculationsTab tenant={tenant} caseId={caseId} />,
    );
    const invalidateQueries = vi.spyOn(queryClient, "invalidateQueries");
    await user.click(
      await screen.findByRole("button", { name: "Rerun current inputs" }),
    );
    await waitFor(() =>
      expect(rerun).toHaveBeenCalledWith(tenant, caseId, run().id),
    );
    expect(invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["findings", tenant, caseId],
    });
  });

  it("rejects fractional forecast periods", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());
    const start = vi.spyOn(riskApi, "startCalculation");
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    const periods = await screen.findByRole("spinbutton", {
      name: "Forecast periods",
    });
    await user.clear(periods);
    await user.type(periods, "1.5");

    expect(screen.getByRole("button", { name: "Run forecast" })).toBeDisabled();
    expect(start).not.toHaveBeenCalled();
  });

  it("pages through run summaries and fetches details separately", async () => {
    const user = userEvent.setup();
    const firstPage = { ...runList([run()]), total: 26, hasMore: true };
    const secondRun = run({ id: "30000000-0000-4000-8000-000000000026" });
    const list = vi
      .spyOn(riskApi, "calculationRuns")
      .mockImplementation((_tenant, _caseId, _scenarioId, _limit, offset) =>
        Promise.resolve(
          offset === 25
            ? { ...runList([secondRun]), total: 26, offset: 25 }
            : firstPage,
        ),
      );
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        Promise.resolve(requestedRunId === secondRun.id ? secondRun : run()),
    );

    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);
    await user.click(await screen.findByRole("button", { name: "Next" }));

    await waitFor(() =>
      expect(list).toHaveBeenCalledWith(tenant, caseId, undefined, 25, 25),
    );
    expect(
      await screen.findByRole("button", { name: /Baseline/ }),
    ).toBeInTheDocument();
    expect(riskApi.calculationRun).toHaveBeenCalledWith(
      tenant,
      caseId,
      secondRun.id,
    );
  });

  it("resets scenario and run selections when the case changes", async () => {
    const user = userEvent.setup();
    const nextCaseId = "90000000-0000-4000-8000-000000000002";
    const nextScenarioId = "20000000-0000-4000-8000-000000000002";
    const nextScenario = {
      ...scenario(),
      id: nextScenarioId,
      caseId: nextCaseId,
      name: "New case baseline",
    };
    const nextRun = run({
      id: "30000000-0000-4000-8000-000000000002",
      caseId: nextCaseId,
      scenarioId: nextScenarioId,
    });
    vi.mocked(riskApi.scenarios).mockImplementation(
      (_tenant, requestedCaseId) =>
        Promise.resolve(
          requestedCaseId === nextCaseId
            ? scenarioWorkspace([nextScenario])
            : scenarioWorkspace(),
        ),
    );
    vi.spyOn(riskApi, "calculationRuns").mockImplementation(
      (_tenant, requestedCaseId) =>
        Promise.resolve(
          requestedCaseId === nextCaseId
            ? runList([nextRun])
            : runList([run()]),
        ),
    );
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, requestedCaseId) =>
        Promise.resolve(requestedCaseId === nextCaseId ? nextRun : run()),
    );
    const start = vi
      .spyOn(riskApi, "startCalculation")
      .mockResolvedValue(nextRun);
    const rerun = vi
      .spyOn(riskApi, "rerunCalculation")
      .mockResolvedValue(nextRun);
    const view = renderWithQuery(
      <CalculationsTab tenant={tenant} caseId={caseId} />,
    );
    expect(await screen.findByLabelText("Forecast scenario")).toHaveTextContent(
      "Baseline",
    );

    view.rerender(<CalculationsTab tenant={tenant} caseId={nextCaseId} />);
    expect(await screen.findByLabelText("Forecast scenario")).toHaveTextContent(
      "New case baseline",
    );
    await user.click(screen.getByRole("button", { name: "Run forecast" }));
    await user.click(
      screen.getByRole("button", { name: "Rerun current inputs" }),
    );

    await waitFor(() =>
      expect(start).toHaveBeenCalledWith(tenant, nextCaseId, {
        scenarioId: nextScenarioId,
        forecastPeriods: 3,
      }),
    );
    expect(rerun).toHaveBeenCalledWith(tenant, nextCaseId, nextRun.id);
  });

  it("formats large decimal outputs without losing precision", async () => {
    const largeRun = run({
      outputs: [{ ...output(), totalAssets: "9007199254740993.1200" }],
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList([largeRun]));
    vi.mocked(riskApi.calculationRun).mockResolvedValue(largeRun);
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("$9,007,199,254,740,993.12"),
    ).toBeInTheDocument();
  });

  it("falls back to plain text for invalid currency codes", async () => {
    const invalidCurrencyRun = run({
      outputs: [{ ...output(), currency: "US1" }],
    });
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(
      runList([invalidCurrencyRun]),
    );
    vi.mocked(riskApi.calculationRun).mockResolvedValue(invalidCurrencyRun);
    renderWithQuery(<CalculationsTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByText("US1 4550.0000")).toBeInTheDocument();
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
