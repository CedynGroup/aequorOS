import type {
  CalculationRunListRead,
  CapitalComparisonRead,
  CapitalProjectionListRead,
  CapitalProjectionRead,
  CapitalProjectionSummaryRead,
  CapitalSummaryRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { CapitalTab } from "./capital-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};
const caseId = "90000000-0000-4000-8000-000000000001";
const scenarioId = "20000000-0000-4000-8000-000000000001";
const runId = "30000000-0000-4000-8000-000000000001";
const now = new Date("2026-07-13T12:00:00Z");

function runList(): CalculationRunListRead {
  return {
    caseId,
    runs: [
      {
        id: runId,
        scenarioId,
        rerunOfRunId: null,
        status: "succeeded",
        engineVersion: "balance-sheet-v1.0.0",
        inputHash: "a".repeat(64),
        forecastPeriods: 2,
        asOfDate: now,
        startedAt: now,
        completedAt: now,
        error: null,
        createdAt: now,
      },
    ],
    latestSuccessfulRunId: runId,
    total: 1,
    limit: 100,
    offset: 0,
    hasMore: false,
  };
}

function scenarioWorkspace(): ScenarioWorkspaceRead {
  return {
    caseId,
    scenarios: [
      {
        id: scenarioId,
        organizationId: tenant.orgId,
        caseId,
        name: "Operating baseline",
        description: null,
        scenarioType: "baseline",
        copiedFromScenarioId: null,
        createdBy: tenant.userId,
        archivedAt: null,
        createdAt: now,
        updatedAt: now,
        assumptions: [],
      },
    ],
    readiness: {
      caseId,
      ready: true,
      scenarioCount: 1,
      completeScenarioCount: 1,
      incompleteScenarioIds: [],
    },
  };
}

function projection(): CapitalProjectionRead {
  return {
    id: "40000000-0000-4000-8000-000000000001",
    organizationId: tenant.orgId,
    caseId,
    scenarioId,
    calculationRunId: runId,
    status: "succeeded",
    engineVersion: "capital-projection-v1.0.0",
    inputHash: "a".repeat(64),
    reportingCurrency: "USD",
    startedAt: now,
    completedAt: now,
    error: null,
    indicators: [
      {
        id: "50000000-0000-4000-8000-000000000001",
        forecastPeriodId: "60000000-0000-4000-8000-000000000001",
        periodNumber: 1,
        equity: "50.0000",
        equityToAssetsRatio: "0.05263158",
        liabilitiesToAssetsRatio: "0.94736842",
        equityChange: "-100.0000",
        pressureLevel: "high",
        evidence: {},
      },
    ],
    findings: [
      {
        finding: {
          id: "70000000-0000-4000-8000-000000000001",
          organizationId: tenant.orgId,
          caseId,
          assessmentId: null,
          runId: null,
          riskType: "leverage_risk",
          title: "Projected capital buffer is thin",
          summary: "The minimum equity-to-assets ratio is 5.26% in period 1.",
          rationale: "Deterministic capital projection rule.",
          severity: "high",
          likelihood: null,
          impact: null,
          confidence: null,
          status: "needs_review",
          dispositionReason: null,
          source: "deterministic_rule",
          ruleId: "capital_thin_buffer",
          ruleVersion: "capital-projection-v1.0.0",
          scoreImpact: null,
          details: {},
          createdAt: now,
          updatedAt: now,
        },
        evidence: [
          {
            id: "80000000-0000-4000-8000-000000000001",
            findingId: "70000000-0000-4000-8000-000000000001",
            documentId: null,
            documentChunkId: null,
            pageNumber: null,
            quote: "The minimum equity-to-assets ratio is 5.26% in period 1.",
            locator: { calculation_run_id: runId },
            relevance: "1",
            createdAt: now,
            document: null,
            chunk: null,
          },
        ],
      },
    ],
    createdBy: tenant.userId,
    createdAt: now,
    updatedAt: now,
  } as unknown as CapitalProjectionRead;
}

function failedProjection(): CapitalProjectionRead {
  return {
    ...projection(),
    id: "40000000-0000-4000-8000-000000000002",
    status: "failed",
    error: {
      code: "forecast_evidence_missing",
      message: "Opening balance evidence is invalid",
      details: {
        forecast_period_id: "60000000-0000-4000-8000-000000000001",
        required_components: ["opening_assets", "opening_liabilities"],
      },
    },
    indicators: [],
    findings: [],
  } as unknown as CapitalProjectionRead;
}

function summary(value: CapitalProjectionRead | null): CapitalSummaryRead {
  return {
    caseId,
    scenarioId: value?.scenarioId ?? null,
    projection: value,
  } as CapitalSummaryRead;
}

function comparison(
  value: CapitalProjectionRead | null,
): CapitalComparisonRead {
  return {
    caseId,
    baseline: value,
    downside: null,
    periods: [],
    diagnostic: null,
  } as unknown as CapitalComparisonRead;
}

function attempts(
  values: CapitalProjectionRead[] = [],
): CapitalProjectionListRead {
  return {
    caseId,
    projections: values.map(projectionSummary),
    total: values.length,
    limit: 25,
    offset: 0,
    hasMore: false,
  };
}

function projectionSummary(
  value: CapitalProjectionRead,
): CapitalProjectionSummaryRead {
  return {
    id: value.id,
    scenarioId: value.scenarioId,
    calculationRunId: value.calculationRunId,
    status: value.status,
    reportingCurrency: value.reportingCurrency,
    startedAt: value.startedAt,
    completedAt: value.completedAt,
    createdAt: value.createdAt,
  };
}

describe("CapitalTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(scenarioWorkspace());
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());
    vi.spyOn(riskApi, "capitalProjections").mockResolvedValue(attempts());
    vi.spyOn(riskApi, "capitalSummary").mockResolvedValue(summary(null));
    vi.spyOn(riskApi, "capitalComparison").mockResolvedValue(comparison(null));
  });

  it("renders the explicit empty state", async () => {
    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);
    expect(
      await screen.findByText("No capital projection"),
    ).toBeInTheDocument();
  });

  it("identifies forecast runs by scenario name and type", async () => {
    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByRole("combobox", { name: "Capital forecast run" }),
    ).toHaveTextContent("Operating baseline (Baseline)");
  });

  it("excludes successful runs for archived scenarios", async () => {
    vi.mocked(riskApi.scenarios).mockResolvedValue({
      ...scenarioWorkspace(),
      scenarios: [
        {
          ...scenarioWorkspace().scenarios[0],
          archivedAt: now,
        },
      ],
    });

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("No successful forecast runs"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate projection" }),
    ).toBeDisabled();
  });

  it("renders an explicit loading state", () => {
    vi.mocked(riskApi.capitalSummary).mockReturnValue(new Promise(() => {}));
    const { container } = renderWithQuery(
      <CapitalTab tenant={tenant} caseId={caseId} />,
    );
    expect(container.querySelector(".animate-pulse")).toBeInTheDocument();
  });

  it("renders indicators, findings, evidence, and incomplete comparison", async () => {
    const value = projection();
    vi.mocked(riskApi.capitalSummary).mockResolvedValue(summary(value));
    vi.mocked(riskApi.capitalComparison).mockResolvedValue(comparison(value));
    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);
    expect(
      await screen.findByText("Projected capital indicators"),
    ).toBeInTheDocument();
    expect(screen.getByText("5.3%")).toBeInTheDocument();
    expect(
      screen.getByText("Projected capital buffer is thin"),
    ).toBeInTheDocument();
    expect(screen.getByText("Evidence")).toBeInTheDocument();
    expect(screen.getByText("Comparison not ready")).toBeInTheDocument();
    expect(screen.getByText("$50.00")).toBeInTheDocument();
    expect(screen.getByText("-$100.00")).toBeInTheDocument();
  });

  it("formats supported capital amounts without Number precision loss", async () => {
    const value = projection();
    value.indicators[0].equity = "9999999999999998.9999";
    vi.mocked(riskApi.capitalSummary).mockResolvedValue(summary(value));
    vi.mocked(riskApi.capitalComparison).mockResolvedValue(comparison(value));

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("$9,999,999,999,999,999.00"),
    ).toBeInTheDocument();
  });

  it("keeps projection history visible when comparison is retired", async () => {
    const value = projection();
    vi.mocked(riskApi.capitalProjections).mockResolvedValue(attempts([value]));
    vi.spyOn(riskApi, "capitalProjection").mockResolvedValue(value);
    vi.mocked(riskApi.capitalComparison).mockRejectedValue(
      new Error("Archived cases cannot be compared"),
    );

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("Projected capital indicators"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Archived cases cannot be compared"),
    ).toBeInTheDocument();
  });

  it("renders every incompatible comparison basis and corrective action", async () => {
    const value = projection();
    vi.mocked(riskApi.capitalSummary).mockResolvedValue(summary(value));
    vi.mocked(riskApi.capitalComparison).mockResolvedValue({
      caseId,
      baseline: value,
      downside: {
        ...value,
        scenarioId: "20000000-0000-4000-8000-000000000002",
      },
      periods: [],
      diagnostic: {
        code: "comparison_basis_mismatch",
        message:
          "Baseline and downside projections use incompatible forecast bases.",
        differingAttributes: [
          "as_of_date",
          "reporting_currency",
          "forecast_horizon",
        ],
        baselineBasis: {
          asOfDate: new Date("2026-06-30T00:00:00Z"),
          reportingCurrency: "USD",
          forecastHorizon: 2,
        },
        downsideBasis: {
          asOfDate: new Date("2026-07-01T00:00:00Z"),
          reportingCurrency: "EUR",
          forecastHorizon: 3,
        },
        correctiveAction:
          "Rerun the other scenario using the matching as-of date, reporting currency, and forecast horizon, then generate a new capital projection.",
      },
    } as CapitalComparisonRead);

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(await screen.findByText("Comparison not ready")).toBeInTheDocument();
    expect(screen.getByText("2026-06-30")).toBeInTheDocument();
    expect(screen.getByText("2026-07-01")).toBeInTheDocument();
    expect(screen.getByText("USD")).toBeInTheDocument();
    expect(screen.getByText("EUR")).toBeInTheDocument();
    expect(screen.getByText("2 periods")).toBeInTheDocument();
    expect(screen.getByText("3 periods")).toBeInTheDocument();
    expect(screen.getByText(/Rerun the other scenario/)).toBeInTheDocument();
  });

  it("generates a projection and exposes mutation state", async () => {
    const user = userEvent.setup();
    let resolveProjection: (value: CapitalProjectionRead) => void = () => {};
    const create = vi.spyOn(riskApi, "createCapitalProjection").mockReturnValue(
      new Promise((resolve) => {
        resolveProjection = resolve;
      }),
    );
    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);
    await user.click(
      await screen.findByRole("button", { name: "Generate projection" }),
    );
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith(tenant, caseId, {
        calculationRunId: runId,
      }),
    );
    expect(screen.getByRole("button", { name: "Generating…" })).toBeDisabled();
    resolveProjection(projection());
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Generate projection" }),
      ).toBeEnabled(),
    );
  });

  it("preserves failed projection diagnostics after generation", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "capitalSummary").mockResolvedValue(
      summary(projection()),
    );
    vi.spyOn(riskApi, "createCapitalProjection").mockResolvedValue(
      failedProjection(),
    );
    vi.spyOn(riskApi, "capitalProjection").mockResolvedValue(
      failedProjection(),
    );
    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    await user.click(
      await screen.findByRole("button", { name: "Generate projection" }),
    );

    expect(
      await screen.findByText("Opening balance evidence is invalid"),
    ).toBeInTheDocument();
    expect(screen.getByText(/opening_assets/)).toBeInTheDocument();
    expect(
      screen.queryByText("Projected capital indicators"),
    ).not.toBeInTheDocument();
  });

  it("restores failed diagnostics from immutable attempt history", async () => {
    vi.mocked(riskApi.capitalProjections).mockResolvedValue(
      attempts([failedProjection(), projection()]),
    );
    vi.spyOn(riskApi, "capitalProjection").mockResolvedValue(
      failedProjection(),
    );
    vi.mocked(riskApi.capitalSummary).mockResolvedValue(summary(projection()));

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByText("Opening balance evidence is invalid"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Capital projection attempt" }),
    ).toHaveTextContent("Failed");
  });

  it("fetches the latest successful active-scenario run outside the first page", async () => {
    const failedRun = {
      ...runList().runs[0],
      id: "failed-run",
      status: "failed" as const,
    };
    vi.mocked(riskApi.calculationRuns).mockImplementation(
      async (_tenant, _caseId, requestedScenarioId) =>
        requestedScenarioId
          ? {
              ...runList(),
              runs: [failedRun],
              total: 101,
              hasMore: true,
            }
          : {
              ...runList(),
              latestSuccessfulRunId: "archived-scenario-run",
              runs: [failedRun],
              total: 101,
              hasMore: true,
            },
    );
    const calculationRun = vi
      .spyOn(riskApi, "calculationRun")
      .mockResolvedValue({
        ...runList().runs[0],
        inputSnapshot: {},
        periods: [],
      } as never);

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    expect(
      await screen.findByRole("combobox", { name: "Capital forecast run" }),
    ).toHaveTextContent("Operating baseline (Baseline)");
    expect(calculationRun).toHaveBeenCalledWith(tenant, caseId, runId);
    expect(riskApi.calculationRuns).toHaveBeenCalledWith(
      tenant,
      caseId,
      scenarioId,
      1,
      0,
    );
  });

  it("explains and disables retired-case capital controls", async () => {
    renderWithQuery(
      <CapitalTab
        tenant={tenant}
        caseId={caseId}
        mutationDisabled
        mutationDisabledReason="retired-case"
      />,
    );

    expect(
      await screen.findByText("Capital mutations unavailable for retired case"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("combobox", { name: "Capital forecast run" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Generate projection" }),
    ).toBeDisabled();
  });

  it("refreshes selected attempt findings after a status update", async () => {
    const user = userEvent.setup();
    const value = projection();
    vi.mocked(riskApi.capitalProjections).mockResolvedValue(attempts([value]));
    const capitalProjection = vi
      .spyOn(riskApi, "capitalProjection")
      .mockResolvedValue(value);
    vi.spyOn(riskApi, "updateFinding").mockResolvedValue({
      ...value.findings[0].finding,
      status: "acknowledged",
    });

    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);

    await screen.findByText("Projected capital buffer is thin");
    await user.click(screen.getByRole("button", { name: "Update" }));

    await waitFor(() => expect(capitalProjection).toHaveBeenCalledTimes(2));
  });

  it("shows read errors and disables mutations in demo mode", async () => {
    vi.mocked(riskApi.capitalSummary).mockRejectedValue(
      new Error("capital unavailable"),
    );
    const { unmount } = renderWithQuery(
      <CapitalTab tenant={tenant} caseId={caseId} />,
    );
    expect(await screen.findByText("capital unavailable")).toBeInTheDocument();
    unmount();
    vi.mocked(riskApi.capitalSummary).mockResolvedValue(summary(null));
    renderWithQuery(
      <CapitalTab tenant={tenant} caseId={caseId} mutationDisabled />,
    );
    expect(
      await screen.findByText("Mutation unavailable in demo mode"),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Generate projection" }),
    ).toBeDisabled();
  });

  it("disables capital finding review mutations in demo mode", async () => {
    const value = projection();
    vi.mocked(riskApi.capitalSummary).mockResolvedValue(summary(value));
    vi.mocked(riskApi.capitalComparison).mockResolvedValue(comparison(value));
    const updateFinding = vi.spyOn(riskApi, "updateFinding");

    renderWithQuery(
      <CapitalTab tenant={tenant} caseId={caseId} mutationDisabled />,
    );

    expect(
      await screen.findByRole("button", { name: "Update" }),
    ).toBeDisabled();
    expect(screen.getByPlaceholderText("Disposition reason")).toBeDisabled();
    expect(updateFinding).not.toHaveBeenCalled();
  });
});
