import type {
  CalculationRunListRead,
  CalculationRunRead,
  CalculationRunSummaryRead,
  LiquidityFindingRead,
  LiquiditySummaryRead,
  ScenarioRead,
  ScenarioWorkspaceRead,
} from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { liquidityReviewClient } from "./liquidity-client";
import { LiquidityTab } from "./liquidity-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};
const now = new Date("2026-07-13T12:00:00Z");

function scenario(id = "scenario-1", name = "Baseline"): ScenarioRead {
  return {
    id,
    organizationId: tenant.orgId,
    caseId: "case-1",
    name,
    description: null,
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
    caseId: "case-1",
    scenarios: items,
    readiness: {
      caseId: "case-1",
      ready: true,
      scenarioCount: items.length,
      completeScenarioCount: items.length,
      incompleteScenarioIds: [],
    },
  };
}

function run(
  id = "run-1",
  scenarioId = "scenario-1",
  createdAt = now,
): CalculationRunSummaryRead {
  return {
    id,
    scenarioId,
    rerunOfRunId: null,
    status: "succeeded",
    engineVersion: "balance-sheet-v1.0.0",
    inputHash: "a".repeat(64),
    forecastPeriods: 3,
    asOfDate: new Date("2026-07-13T00:00:00Z"),
    startedAt: createdAt,
    completedAt: createdAt,
    error: null,
    createdAt,
  };
}

function runList(
  items: CalculationRunSummaryRead[] = [run()],
): CalculationRunListRead {
  return {
    caseId: "case-1",
    runs: items,
    latestSuccessfulRunId: items[0]?.id ?? null,
    total: items.length,
    limit: 25,
    offset: 0,
    hasMore: false,
  };
}

function runDetail(
  item: CalculationRunSummaryRead = run(),
): CalculationRunRead {
  return {
    ...item,
    caseId: "case-1",
    createdBy: tenant.userId,
    inputSchemaVersion: "balance-sheet-input-v1",
    inputs: {},
    organizationId: tenant.orgId,
    outputSchemaVersion: "balance-sheet-output-v1",
    outputs: [],
    updatedAt: item.createdAt,
  };
}

function finding(
  overrides: Partial<LiquidityFindingRead> = {},
): LiquidityFindingRead {
  return {
    id: "finding-1",
    calculationRunId: "run-1",
    ruleId: "liquidity.sources_coverage",
    ruleVersion: "liquidity-v1.0.0",
    title: "Thin liquidity sources coverage",
    summary: "Sources cover 0.75x of uses.",
    rationale: "Coverage is below 1.20x.",
    severity: "high",
    status: "open",
    dispositionReason: null,
    evidence: [
      {
        id: "evidence-1",
        sourceType: "forecast_output",
        label: "Forecast period 1",
        sourceUrl:
          "/cases/case-1?tab=calculations#calculation-run-run-1-forecast-period-1",
        locator: { period_number: 1 },
        quote: "Coverage is below 1.20x.",
      },
    ],
    createdAt: new Date("2026-07-13T12:00:00Z"),
    updatedAt: new Date("2026-07-13T12:00:00Z"),
    ...overrides,
  };
}

function summary(
  overrides: Partial<LiquiditySummaryRead> = {},
): LiquiditySummaryRead {
  return {
    caseId: "case-1",
    scenarioId: "scenario-1",
    calculationRunId: "run-1",
    calculationInputHash: "abcdef1234567890",
    analysisVersion: "liquidity-v1.0.0",
    status: "ready",
    currency: "USD",
    asOfDate: "2026-07-13",
    generatedAt: "2026-07-13T12:00:00Z",
    metrics: [
      {
        key: "minimum_cash_balance",
        label: "Minimum cash balance",
        value: "-500.0000",
        unit: "USD",
        periodNumber: 1,
        periodEnd: "2027-07-13",
        description: "Lowest projected ending cash balance.",
      },
    ],
    findings: [finding()],
    ...overrides,
  };
}

describe("LiquidityTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    Object.defineProperties(Element.prototype, {
      hasPointerCapture: { configurable: true, value: () => false },
      releasePointerCapture: { configurable: true, value: () => undefined },
      scrollIntoView: { configurable: true, value: () => undefined },
      setPointerCapture: { configurable: true, value: () => undefined },
    });
    vi.spyOn(riskApi, "scenarios").mockResolvedValue(scenarioWorkspace());
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());
    vi.spyOn(riskApi, "calculationRun").mockImplementation(
      (_tenant, _caseId, runId) => Promise.resolve(runDetail(run(runId))),
    );
  });

  it("renders explicit loading and empty states", async () => {
    vi.spyOn(liquidityReviewClient, "summary").mockResolvedValue({
      ...summary(),
      status: "not_calculated",
      calculationRunId: null,
      calculationInputHash: null,
      scenarioId: null,
      currency: null,
      asOfDate: null,
      generatedAt: null,
      metrics: [],
      findings: [],
    });

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    expect(
      screen.getByLabelText("Loading liquidity analysis"),
    ).toBeInTheDocument();
    expect(
      await screen.findByText(
        "Liquidity analysis not available for this run — rerun to generate it.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Open Forecast to rerun" }),
    ).toHaveAttribute(
      "href",
      "/cases/case-1?tab=calculations#calculation-run-run-1-forecast-period-1",
    );
  });

  it("renders metrics, shared finding card, and evidence links", async () => {
    vi.spyOn(liquidityReviewClient, "summary").mockResolvedValue(summary());

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    expect(
      await screen.findByText("Liquidity risk summary"),
    ).toBeInTheDocument();
    expect(screen.getByText(/Baseline · run run-1/)).toBeInTheDocument();
    expect(screen.getByText("-$500.00")).toBeInTheDocument();
    expect(
      screen.getByText("Thin liquidity sources coverage"),
    ).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("Supporting evidence (1)"));
    expect(
      screen.getByRole("link", { name: /Forecast period 1/ }),
    ).toHaveAttribute(
      "href",
      "/cases/case-1?tab=calculations#calculation-run-run-1-forecast-period-1",
    );
  });

  it("falls back to plain text for invalid currency codes", async () => {
    vi.spyOn(liquidityReviewClient, "summary").mockResolvedValue(
      summary({
        currency: "US1",
        metrics: [
          {
            ...summary().metrics[0],
            unit: "US1",
          },
        ],
      }),
    );

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    expect(await screen.findByText("US1 -500.0000")).toBeInTheDocument();
  });

  it("formats large monetary metrics without losing precision", async () => {
    vi.spyOn(liquidityReviewClient, "summary").mockResolvedValue(
      summary({
        metrics: [
          {
            ...summary().metrics[0],
            value: "9007199254740993.0000",
          },
        ],
      }),
    );

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    expect(
      await screen.findByText("$9,007,199,254,740,993.00"),
    ).toBeInTheDocument();
  });

  it("selects an explicit scenario and run and scopes the summary", async () => {
    const user = userEvent.setup();
    const olderRun = run(
      "run-older",
      "scenario-1",
      new Date("2026-07-12T12:00:00Z"),
    );
    const downsideRun = run("run-2", "scenario-2");
    vi.mocked(riskApi.scenarios).mockResolvedValue(
      scenarioWorkspace([scenario(), scenario("scenario-2", "Downside")]),
    );
    vi.mocked(riskApi.calculationRuns).mockImplementation(
      (_tenant, _caseId, requestedScenarioId) =>
        Promise.resolve(
          requestedScenarioId === "scenario-2"
            ? runList([downsideRun])
            : runList([run(), olderRun]),
        ),
    );
    const loadSummary = vi
      .spyOn(liquidityReviewClient, "summary")
      .mockImplementation(
        (_tenant, _caseId, requestedScenarioId, requestedRunId) =>
          Promise.resolve(
            summary({
              scenarioId: requestedScenarioId,
              calculationRunId: requestedRunId,
            }),
          ),
      );

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    await waitFor(() =>
      expect(loadSummary).toHaveBeenCalledWith(
        tenant,
        "case-1",
        "scenario-1",
        "run-1",
      ),
    );
    await user.click(screen.getByLabelText("Liquidity forecast run"));
    await user.click(await screen.findByRole("option", { name: /run-older/ }));
    await waitFor(() =>
      expect(loadSummary).toHaveBeenCalledWith(
        tenant,
        "case-1",
        "scenario-1",
        "run-older",
      ),
    );

    await user.click(screen.getByLabelText("Liquidity scenario"));
    await user.click(await screen.findByRole("option", { name: "Downside" }));
    await waitFor(() =>
      expect(loadSummary).toHaveBeenCalledWith(
        tenant,
        "case-1",
        "scenario-2",
        "run-2",
      ),
    );
    expect(screen.getByText(/Downside · run run-2/)).toBeInTheDocument();
  });

  it("keeps the latest successful run selectable outside the current history page", async () => {
    const user = userEvent.setup();
    const latest = run(
      "run-latest-success",
      "scenario-1",
      new Date("2026-07-01T12:00:00Z"),
    );
    const historical = run(
      "run-historical-success",
      "scenario-1",
      new Date("2026-06-30T12:00:00Z"),
    );
    const failedPage = Array.from({ length: 25 }, (_, index) => ({
      ...run(`run-failed-${index}`),
      status: "failed" as const,
    }));
    vi.mocked(riskApi.calculationRuns).mockImplementation(
      (_tenant, _caseId, _scenarioId, _limit, offset) =>
        Promise.resolve({
          ...runList(offset === 25 ? [historical] : failedPage),
          latestSuccessfulRunId: latest.id,
          total: 26,
          offset: offset ?? 0,
          hasMore: offset !== 25,
        }),
    );
    vi.mocked(riskApi.calculationRun).mockImplementation(
      (_tenant, _caseId, requestedRunId) =>
        Promise.resolve(
          runDetail(requestedRunId === historical.id ? historical : latest),
        ),
    );
    const loadSummary = vi
      .spyOn(liquidityReviewClient, "summary")
      .mockImplementation((_tenant, _caseId, scenarioId, calculationRunId) =>
        Promise.resolve(summary({ scenarioId, calculationRunId })),
      );

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    await waitFor(() =>
      expect(loadSummary).toHaveBeenCalledWith(
        tenant,
        "case-1",
        "scenario-1",
        latest.id,
      ),
    );
    expect(screen.getByLabelText("Liquidity forecast run")).toHaveTextContent(
      "run-late...cess",
    );

    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(riskApi.calculationRuns).toHaveBeenCalledWith(
        tenant,
        "case-1",
        "scenario-1",
        25,
        25,
      ),
    );
    await user.click(screen.getByLabelText("Liquidity forecast run"));
    await user.click(
      await screen.findByRole("option", { name: /run-hist\.\.\.cess/ }),
    );
    await waitFor(() =>
      expect(loadSummary).toHaveBeenCalledWith(
        tenant,
        "case-1",
        "scenario-1",
        historical.id,
      ),
    );
  });

  it("acknowledges and dismisses findings with explicit mutation states", async () => {
    const user = userEvent.setup();
    vi.spyOn(liquidityReviewClient, "summary").mockResolvedValue(summary());
    const review = vi
      .spyOn(liquidityReviewClient, "review")
      .mockResolvedValue(finding({ status: "acknowledged" }));
    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    await user.click(
      await screen.findByRole("button", { name: "Acknowledge" }),
    );
    await waitFor(() => {
      expect(review).toHaveBeenCalledWith(tenant, "case-1", "finding-1", {
        action: "acknowledge",
        reason: undefined,
      });
    });

    await user.type(
      screen.getByLabelText(
        "Dismissal reason for Thin liquidity sources coverage",
      ),
      "Management has committed funding",
    );
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => {
      expect(review).toHaveBeenLastCalledWith(tenant, "case-1", "finding-1", {
        action: "dismiss",
        reason: "Management has committed funding",
      });
    });
  });

  it("renders summary load errors", async () => {
    vi.spyOn(liquidityReviewClient, "summary").mockRejectedValue(
      new Error("Liquidity service unavailable"),
    );

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    expect(
      await screen.findByText("Liquidity service unavailable"),
    ).toBeInTheDocument();
  });
});
