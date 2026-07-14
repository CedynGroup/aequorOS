import type {
  LiquidityFindingRead,
  LiquiditySummaryRead,
} from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { liquidityReviewClient } from "./liquidity-client";
import { LiquidityTab } from "./liquidity-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

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
          "/api/v1/cases/case-1/calculation-runs/run-1#forecast-period-1",
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
  beforeEach(() => vi.restoreAllMocks());

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
      await screen.findByText("No liquidity analysis"),
    ).toBeInTheDocument();
  });

  it("renders metrics, shared finding card, and evidence links", async () => {
    vi.spyOn(liquidityReviewClient, "summary").mockResolvedValue(summary());

    renderWithQuery(<LiquidityTab tenant={tenant} caseId="case-1" />);

    expect(
      await screen.findByText("Liquidity risk summary"),
    ).toBeInTheDocument();
    expect(screen.getByText("-$500")).toBeInTheDocument();
    expect(
      screen.getByText("Thin liquidity sources coverage"),
    ).toBeInTheDocument();
    await userEvent.setup().click(screen.getByText("Supporting evidence (1)"));
    expect(
      screen.getByRole("link", { name: /Forecast period 1/ }),
    ).toHaveAttribute(
      "href",
      expect.stringContaining("/api/v1/cases/case-1/calculation-runs/run-1"),
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
