import type { CaseDecisionRead } from "@aequoros/risk-service-api";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { riskApi, type TenantHeaders } from "../../lib/api";
import { DEFAULT_ORG_ID, DEFAULT_USER_ID } from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { mockCase } from "../demo-data/demo-data";
import { CaseWorkspace } from "./case-workspace";

vi.mock("../capital/capital-tab", () => ({
  CapitalTab: ({
    mutationDisabled,
    mutationDisabledReason,
  }: {
    mutationDisabled: boolean;
    mutationDisabledReason: string;
  }) => (
    <div>
      Capital controls: {String(mutationDisabled)} · {mutationDisabledReason}
    </div>
  ),
}));

vi.mock("../calculations/calculations-tab", () => ({
  CalculationsTab: ({
    mutationDisabled,
    mutationDisabledReason,
  }: {
    mutationDisabled: boolean;
    mutationDisabledReason: string;
  }) => (
    <div>
      Forecast controls: {String(mutationDisabled)} · {mutationDisabledReason}
    </div>
  ),
}));

vi.mock("../findings/findings-tab", () => ({
  FindingsTab: ({
    mutationDisabled,
    mutationDisabledReason,
  }: {
    mutationDisabled: boolean;
    mutationDisabledReason: string;
  }) => (
    <div>
      Finding controls: {String(mutationDisabled)} · {mutationDisabledReason}
    </div>
  ),
}));

vi.mock("../liquidity/liquidity-tab", () => ({
  LiquidityTab: ({
    mutationDisabled,
    mutationDisabledReason,
  }: {
    mutationDisabled: boolean;
    mutationDisabledReason: string;
  }) => (
    <div>
      Liquidity controls: {String(mutationDisabled)} · {mutationDisabledReason}
    </div>
  ),
}));

type WorkspaceProps = Parameters<typeof CaseWorkspace>[0];

const tenant: TenantHeaders = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};

const caseId = "90000000-0000-4000-8000-000000000001";

function renderWorkspace(overrides: Partial<WorkspaceProps> = {}) {
  const props: WorkspaceProps = {
    tenant,
    caseId,
    activeTab: "overview",
    reportMode: "json",
    updateSearch: vi.fn<WorkspaceProps["updateSearch"]>(),
    caseQuery: {
      data: undefined,
      error: null,
      isError: false,
      isFetching: false,
    },
    mockCaseData: mockCase(DEFAULT_ORG_ID, caseId),
    mockWorkspace: false,
    ...overrides,
  };

  return {
    props,
    ...renderWithQuery(<CaseWorkspace {...props} />),
  };
}

describe("CaseWorkspace", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("prompts the operator when no case is selected", () => {
    renderWorkspace({
      caseId: undefined,
      mockCaseData: undefined,
    });

    expect(screen.getByText("No case selected")).toBeInTheDocument();
    expect(
      screen.getByText(
        "Select a case from the queue or current case selector.",
      ),
    ).toBeInTheDocument();
  });

  it("renders overview summary and disables deprecated single-case actions", () => {
    renderWorkspace();

    expect(
      screen.getByText("Covenant review - Northstar Foods"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Assign, unassign, and archive"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Single-case actions are left disabled/),
    ).toBeInTheDocument();
  });

  it("shows the assessment reference beside a live case score", async () => {
    vi.spyOn(riskApi, "scores").mockResolvedValue([
      {
        id: "score-1",
        organizationId: DEFAULT_ORG_ID,
        caseId,
        assessmentId: "assessment-1",
        runId: "run-1",
        runReference: "Score 2026-07-14 run 2",
        score: 82,
        riskLevel: "high",
        scoringVersion: "demo-v1",
        inputHash: "a".repeat(64),
        inputSnapshot: {},
        ruleResults: [],
        createdAt: new Date("2026-07-14T12:00:00Z"),
      },
    ]);
    const caseData = mockCase(DEFAULT_ORG_ID, caseId);

    renderWorkspace({
      mockCaseData: undefined,
      caseQuery: {
        data: caseData,
        error: null,
        isError: false,
        isFetching: false,
      },
    });

    expect(
      await screen.findByText("Score 2026-07-14 run 2"),
    ).toBeInTheDocument();
  });

  it("shows a stable assessment reference for a frontend demo score", () => {
    const caseData = mockCase(DEFAULT_ORG_ID, caseId);

    renderWorkspace({ mockCaseData: caseData });

    expect(screen.getByText(caseData.scoreRunReference)).toBeInTheDocument();
    expect(screen.queryByText("Reference unavailable")).not.toBeInTheDocument();
  });

  it("keeps analysis verticals in case-detail tabs rather than workflow navigation", () => {
    renderWorkspace();

    expect(screen.getByRole("tab", { name: "Forecast" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Liquidity" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Liquidity" }),
    ).not.toBeInTheDocument();
  });

  it("loads HTML reports in the report preview mode", async () => {
    const reportHtml = vi
      .spyOn(riskApi, "reportHtml")
      .mockResolvedValue("<main>HTML report</main>");

    renderWorkspace({
      activeTab: "report",
      reportMode: "html",
    });

    await waitFor(() => {
      expect(reportHtml).toHaveBeenCalledWith(tenant, caseId);
    });
    expect(
      await screen.findByTitle("Risk report HTML preview"),
    ).toHaveAttribute("srcdoc", "<main>HTML report</main>");
  });

  it("submits the decision form with the expected payload", async () => {
    const user = userEvent.setup();
    vi.spyOn(riskApi, "decisions").mockResolvedValue([]);
    const createDecision = vi
      .spyOn(riskApi, "createDecision")
      .mockResolvedValue({
        id: "decision-1",
        organizationId: DEFAULT_ORG_ID,
        caseId,
        decision: "approved",
        previousDecision: null,
        reason: "Ready for approval",
        decidedBy: DEFAULT_USER_ID,
        decidedByDisplayName: "Demo User One",
        createdAt: new Date(),
      } as CaseDecisionRead);

    renderWorkspace({
      activeTab: "decisions",
    });

    await user.type(
      await screen.findByPlaceholderText("Reason"),
      "Ready for approval",
    );
    await user.click(screen.getByRole("button", { name: "Submit decision" }));

    await waitFor(() => {
      expect(createDecision).toHaveBeenCalledWith(tenant, caseId, {
        decision: "approved",
        reason: "Ready for approval",
      });
    });
  });

  it("shows the resolved decision reviewer without exposing their identifier", async () => {
    vi.spyOn(riskApi, "decisions").mockResolvedValue([
      {
        id: "decision-1",
        organizationId: DEFAULT_ORG_ID,
        caseId,
        decision: "approved",
        previousDecision: null,
        reason: "Ready for approval",
        decidedBy: DEFAULT_USER_ID,
        decidedByDisplayName: "Ama Mensah",
        createdAt: new Date(),
      } as CaseDecisionRead,
    ]);

    renderWorkspace({ activeTab: "decisions" });

    expect(
      await screen.findByText("Decided by Ama Mensah"),
    ).toBeInTheDocument();
    expect(screen.queryByText(DEFAULT_USER_ID)).not.toBeInTheDocument();
  });

  it("retires capital mutations when the selected case is archived", async () => {
    renderWorkspace({
      activeTab: "capital",
      mockCaseData: {
        ...mockCase(DEFAULT_ORG_ID, caseId),
        archivedAt: new Date("2026-07-14T12:00:00Z"),
      },
    });

    expect(
      await screen.findByText("Capital controls: true · retired-case"),
    ).toBeInTheDocument();
  });

  it("retires shared finding mutations when the selected case is archived", async () => {
    renderWorkspace({
      activeTab: "findings",
      mockCaseData: {
        ...mockCase(DEFAULT_ORG_ID, caseId),
        status: "archived",
      },
    });

    expect(
      await screen.findByText("Finding controls: true · retired-case"),
    ).toBeInTheDocument();
  });

  it("disables shared finding mutations in demo mode", async () => {
    renderWorkspace({
      activeTab: "findings",
      mockWorkspace: true,
    });

    expect(
      await screen.findByText("Finding controls: true · demo"),
    ).toBeInTheDocument();
  });

  it("retires forecast mutations when the selected case is archived", async () => {
    renderWorkspace({
      activeTab: "calculations",
      mockCaseData: {
        ...mockCase(DEFAULT_ORG_ID, caseId),
        status: "archived",
      },
    });

    expect(
      await screen.findByText("Forecast controls: true · retired-case"),
    ).toBeInTheDocument();
  });

  it("disables forecast mutations in demo mode", async () => {
    renderWorkspace({
      activeTab: "calculations",
      mockWorkspace: true,
    });

    expect(
      await screen.findByText("Forecast controls: true · demo"),
    ).toBeInTheDocument();
  });

  it("retires liquidity mutations when the selected case is archived", async () => {
    renderWorkspace({
      activeTab: "liquidity",
      mockCaseData: {
        ...mockCase(DEFAULT_ORG_ID, caseId),
        archivedAt: new Date("2026-07-14T12:00:00Z"),
      },
    });

    expect(
      await screen.findByText("Liquidity controls: true · retired-case"),
    ).toBeInTheDocument();
  });

  it("disables liquidity mutations in demo mode", async () => {
    renderWorkspace({
      activeTab: "liquidity",
      mockWorkspace: true,
    });

    expect(
      await screen.findByText("Liquidity controls: true · demo"),
    ).toBeInTheDocument();
  });
});
