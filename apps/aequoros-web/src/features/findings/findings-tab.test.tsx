import type { FindingRead } from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { FindingsTab } from "./findings-tab";

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

function finding(overrides: Partial<FindingRead> = {}): FindingRead {
  return {
    id: "finding-1",
    organizationId: DEFAULT_ORG_ID,
    caseId: "case-1",
    assessmentId: null,
    runId: null,
    riskType: "liquidity_gap",
    title: "Cash conversion cycle widened",
    summary: "Receivables increased faster than revenue.",
    rationale: null,
    severity: "high",
    likelihood: null,
    impact: null,
    confidence: null,
    status: "accepted",
    dispositionReason: "Accepted for covenant monitoring.",
    source: "manual",
    ruleId: "liquidity_cash_conversion",
    ruleVersion: null,
    scoreImpact: 24,
    details: {},
    createdAt: new Date(),
    updatedAt: new Date(),
    ...overrides,
  };
}

describe("FindingsTab", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the create form and finding update controls", async () => {
    vi.spyOn(riskApi, "findings").mockResolvedValue([finding()]);

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    expect(await screen.findByText("Create manual finding")).toBeInTheDocument();
    expect(await screen.findByText("Cash conversion cycle widened")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Update" })).toBeInTheDocument();
  });

  it("submits manual finding payloads", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "findings").mockResolvedValue([]);
    const createFinding = vi.spyOn(riskApi, "createFinding").mockResolvedValue(finding({
      title: "Missing management commentary",
      summary: "Reviewer needs current quarter commentary.",
    }));

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    await user.type(await screen.findByPlaceholderText("Title"), "Missing management commentary");
    await user.type(screen.getByPlaceholderText("Summary"), "Reviewer needs current quarter commentary.");
    await user.click(screen.getByRole("button", { name: "Create finding" }));

    await waitFor(() => {
      expect(createFinding).toHaveBeenCalledWith(tenant, "case-1", {
        riskType: "manual_review",
        title: "Missing management commentary",
        summary: "Reviewer needs current quarter commentary.",
        severity: "medium",
        details: {},
      });
    });
  });

  it("submits finding status updates", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "findings").mockResolvedValue([finding()]);
    const updateFinding = vi.spyOn(riskApi, "updateFinding").mockResolvedValue(finding());

    renderWithQuery(<FindingsTab tenant={tenant} caseId="case-1" />);

    await user.click(await screen.findByRole("button", { name: "Update" }));

    await waitFor(() => {
      expect(updateFinding).toHaveBeenCalledWith(tenant, "finding-1", {
        status: "accepted",
        dispositionReason: "Accepted for covenant monitoring.",
      });
    });
  });
});
