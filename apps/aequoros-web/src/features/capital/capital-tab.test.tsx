import type {
  CalculationRunListRead,
  CapitalComparisonRead,
  CapitalProjectionRead,
  CapitalSummaryRead,
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
  } as unknown as CapitalComparisonRead;
}

describe("CapitalTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(riskApi, "calculationRuns").mockResolvedValue(runList());
    vi.spyOn(riskApi, "capitalSummary").mockResolvedValue(summary(null));
    vi.spyOn(riskApi, "capitalComparison").mockResolvedValue(comparison(null));
  });

  it("renders the explicit empty state", async () => {
    renderWithQuery(<CapitalTab tenant={tenant} caseId={caseId} />);
    expect(
      await screen.findByText("No capital projection"),
    ).toBeInTheDocument();
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
});
